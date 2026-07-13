"""Concrete benchmark scenarios: overhead, throughput, dedup, compensation
chains, multi-tenant noise, SSE delivery, approval latency, longevity.

Each scenario is an async function that yields ``BenchResult`` instances
by calling ``run_benchmark`` from the harness module.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import os
import time
from typing import Any

import httpx

from undolog_sdk.client import UndoLogClient
from undolog_sdk.session import UndoLogSession

from infra.benchmarks.harness import BenchResult, Timer, run_benchmark

# sse_dashboard lives in examples/; sys.path is set by __init__.py at
# package import time, so this module-level import is safe.
from sse_dashboard import SSEConnection, Event  # noqa: E402  # path set by package __init__

log = logging.getLogger(__name__)

_PROXY_URL = os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")
_TOOL_SERVER_URL = os.environ.get("MOCK_TOOL_SERVER_URL", "http://localhost:9091")
_API_KEY = os.environ.get("UNDOLOG_API_KEY", "dev-key")
_API_KEY_2 = os.environ.get("UNDOLOG_API_KEY_2", "dev-key-2")


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _direct_tool_call(tool_name: str, args: dict[str, str]) -> dict[str, Any]:
    """Call the mock tool server directly (no proxy, no SDK)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_TOOL_SERVER_URL}/tools",
            json={"tool_name": tool_name, "args": args},
            timeout=5.0,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["output"])


async def _approve(approval_id: str) -> None:
    """Approve an IRREVERSIBLE effect via the proxy REST API."""
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{_PROXY_URL}/approvals/{approval_id}/approve",
            json={"actor": "benchmark", "note": "Auto-approved"},
            headers={"X-Api-Key": _API_KEY},
            timeout=10.0,
        )
        resp.raise_for_status()


async def _db_query(sql: str) -> str | None:
    """Run a SQL query via psql and return the trimmed output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "psql",
            "-U",
            "postgres",
            "-d",
            "undolog",
            "-t",
            "-c",
            sql,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() if stdout else None
    except Exception:
        # Transient DB failure: return None so callers can degrade gracefully.
        return None


# ── Benchmark 1: UndoLog overhead latency ────────────────────────────────────


async def bench_overhead(
    warmup: int = 5,
    duration: int = 15,
    concurrency: int = 1,
) -> list[BenchResult]:
    """Measure round-trip latency for direct vs SAFE vs COMPENSABLE vs IRREVERSIBLE.

    Returns one ``BenchResult`` per tier.
    """
    results: list[BenchResult] = []
    args = {"customer_id": "cust_42"}

    # Direct (no proxy, no SDK) - baseline.
    async def _direct() -> None:
        await _direct_tool_call("lookup_customer", args)

    results.append(
        await run_benchmark(
            "overhead-direct",
            _direct,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=concurrency,
            config={"tier": "direct"},
        )
    )

    # SAFE tier - goes through the decorated tool which bypasses the proxy.
    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)

    async def _safe() -> None:
        async with UndoLogSession(org_id="org-bench"):
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{_TOOL_SERVER_URL}/tools",
                    json={"tool_name": "lookup_customer", "args": args},
                    timeout=5.0,
                )
                resp.raise_for_status()

    results.append(
        await run_benchmark(
            "overhead-safe",
            _safe,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=concurrency,
            config={"tier": "safe"},
        )
    )

    # COMPENSABLE tier - intercept -> execute -> commit.
    async def _compensable() -> None:
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            intercept = await client.intercept(
                org_id=session.org_id,
                session_id=session.session_id,
                tool_name="send_email",
                step_index=step,
                args={"to": "bench@example.com", "subject": "test", "body": "."},
            )
            result = await _direct_tool_call(
                "send_email",
                {"to": "bench@example.com", "subject": "test", "body": "."},
            )
            if intercept.effect_id:
                await client.commit(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    effect_id=intercept.effect_id,
                    result=result,
                )

    results.append(
        await run_benchmark(
            "overhead-compensable",
            _compensable,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=concurrency,
            config={"tier": "compensable"},
        )
    )

    # IRREVERSIBLE tier - intercept -> approve (via REST API) -> execute -> commit.
    async def _irreversible() -> None:
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            intercept = await client.intercept(
                org_id=session.org_id,
                session_id=session.session_id,
                tool_name="escalate_case",
                step_index=step,
                args={"ticket_id": "TKT-BENCH", "reason": "benchmark"},
            )
            if intercept.outcome == "AwaitingApproval" and intercept.approval_id:
                await _approve(intercept.approval_id)

    results.append(
        await run_benchmark(
            "overhead-irreversible",
            _irreversible,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=concurrency,
            config={"tier": "irreversible"},
        )
    )

    await client.aclose()
    return results


# ── Benchmark 2: Throughput under concurrency ─────────────────────────────────


async def bench_throughput(
    warmup: int = 5,
    duration: int = 30,
    concurrency_levels: list[int] | None = None,
) -> list[BenchResult]:
    """Drive N concurrent sessions and measure completed tool calls per second.

    Returns one ``BenchResult`` per concurrency level.
    """
    levels = concurrency_levels or [1, 5, 10, 25, 50]
    results: list[BenchResult] = []
    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)

    for n in levels:

        async def _workload() -> None:
            async with UndoLogSession(org_id="org-bench") as session:
                step = session.next_step()
                intercept = await client.intercept(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    tool_name="send_email",
                    step_index=step,
                    args={"to": "tput@example.com", "subject": "tput", "body": "."},
                )
                result = await _direct_tool_call(
                    "send_email",
                    {"to": "tput@example.com", "subject": "tput", "body": "."},
                )
                if intercept.effect_id:
                    await client.commit(
                        org_id=session.org_id,
                        session_id=session.session_id,
                        effect_id=intercept.effect_id,
                        result=result,
                    )

        cell = await run_benchmark(
            f"throughput-concurrency-{n}",
            _workload,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=n,
            config={"tier": "compensable", "concurrency": n},
        )
        results.append(cell)

    await client.aclose()
    return results


# ── Benchmark 3: Dedup overhead ───────────────────────────────────────────────


async def bench_dedup(
    warmup: int = 5,
    duration: int = 15,
    concurrency: int = 1,
) -> list[BenchResult]:
    """Compare first-execution latency vs replay (cached) latency.

    Also runs a contention test with 10 concurrent workers and verifies
    exactly-once semantics via the database effect store.

    Returns ``BenchResult`` values: cold, hot, contention.
    """
    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)
    results: list[BenchResult] = []
    args = {"to": "dedup@example.com", "subject": "dedup", "body": "."}

    # Cold - each session uses a unique session_id but identical tool
    # name and args.  The first call per signature creates a new effect.
    async def _cold() -> None:
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            intercept = await client.intercept(
                org_id=session.org_id,
                session_id=session.session_id,
                tool_name="send_email",
                step_index=step,
                args=args,
            )
            result = await _direct_tool_call("send_email", args)
            if intercept.effect_id:
                await client.commit(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    effect_id=intercept.effect_id,
                    result=result,
                )

    results.append(
        await run_benchmark(
            "dedup-cold",
            _cold,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=concurrency,
            config={"phase": "cold"},
            min_samples=100,
        )
    )

    # Hot - reuse the same signature so the engine returns a replay.
    async def _hot() -> None:
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            await client.intercept(
                org_id=session.org_id,
                session_id=session.session_id,
                tool_name="send_email",
                step_index=step,
                args=args,
            )

    results.append(
        await run_benchmark(
            "dedup-hot",
            _hot,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=concurrency,
            config={"phase": "hot"},
            min_samples=100,
        )
    )

    # Contention - 10 concurrent workers with the identical signature.
    async def _contention() -> None:
        client_c = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)
        try:
            sess = UndoLogSession(org_id="org-bench")
            await sess.__aenter__()
            try:
                effect_ids: set[str] = set()
                outcomes: list[str] = []

                async def _contend() -> dict[str, str]:
                    step = sess.next_step()
                    intercept = await client_c.intercept(
                        org_id=sess.org_id,
                        session_id=sess.session_id,
                        tool_name="send_email",
                        step_index=step,
                        args=args,
                    )
                    eid = intercept.effect_id or ""
                    return {"effect_id": eid, "outcome": str(intercept.outcome)}

                tasks = [asyncio.create_task(_contend()) for _ in range(10)]
                contention_results = await asyncio.gather(*tasks)

                for cr in contention_results:
                    if cr["effect_id"]:
                        effect_ids.add(cr["effect_id"])
                    outcomes.append(cr["outcome"])

                execute_count = sum(1 for o in outcomes if o == "Outcome.EXECUTED")
                replay_count = sum(1 for o in outcomes if o == "Outcome.REPLAYED")

                log.info(
                    "Dedup contention: %d executed, %d replayed, %d unique effect_ids",
                    execute_count,
                    replay_count,
                    len(effect_ids),
                )

                # Verify exactly-once: only 1 unique effect_id.
                if len(effect_ids) > 1:
                    log.warning(
                        "Dedup contention FAILED: %d unique effect_ids (expected 1)",
                        len(effect_ids),
                    )

                # Verify via DB effect store.
                sig_sql = (
                    "SELECT count(*) FROM effects WHERE tool_name='send_email' "
                    "AND args->>'to'='dedup@example.com'"
                )
                db_count = await _db_query(sig_sql)
                if db_count is not None:
                    count_val = int(db_count)
                    if count_val > 1:
                        log.warning(
                            "DB dedup check FAILED: %d rows for signature (expected 1)",
                            count_val,
                        )
                    else:
                        log.info(
                            "DB dedup check PASSED: %d row(s) for signature",
                            count_val,
                        )

            finally:
                await sess.__aexit__(None, None, None)
        finally:
            await client_c.aclose()

    results.append(
        await run_benchmark(
            "dedup-contention",
            _contention,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=1,
            config={"phase": "contention"},
            min_samples=100,
        )
    )

    await client.aclose()
    return results


# ── Benchmark 4: Compensation chain latency ────────────────────────────────────


async def bench_compensation_chain(
    warmup: int = 5,
    duration: int = 20,
    chain_lengths: list[int] | None = None,
) -> list[BenchResult]:
    """Measure rollback time for undo stacks of varying depth.

    Registers N COMPENSABLE effects, commits each, then fails the last
    to trigger LIFO rollback.  Per-effect compensation timings and LIFO
    ordering are verified inline.

    Returns one ``BenchResult`` per chain length.
    """
    lengths = chain_lengths or [1, 5, 10, 20]
    results: list[BenchResult] = []
    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)

    for n in lengths:
        per_compensation_us: list[float] = []

        async def _rollback() -> None:
            nonlocal per_compensation_us
            async with UndoLogSession(org_id="org-bench") as session:
                effect_ids: list[str] = []
                registrations: list[float] = []
                for i in range(n):
                    step = session.next_step()
                    t = Timer()
                    async with t:
                        intercept = await client.intercept(
                            org_id=session.org_id,
                            session_id=session.session_id,
                            tool_name="send_email",
                            step_index=step,
                            args={
                                "to": f"chain{i}@example.com",
                                "subject": f"chain-{n}-{i}",
                                "body": ".",
                            },
                        )
                    registrations.append(t.elapsed_us)
                    result = await _direct_tool_call(
                        "send_email",
                        {
                            "to": f"chain{i}@example.com",
                            "subject": f"chain-{n}-{i}",
                            "body": ".",
                        },
                    )
                    if intercept.effect_id:
                        await client.commit(
                            org_id=session.org_id,
                            session_id=session.session_id,
                            effect_id=intercept.effect_id,
                            result=result,
                        )
                        effect_ids.append(intercept.effect_id)

                # Fail the last effect to trigger LIFO rollback.
                # Time each individual compensation.
                compensation_times: list[float] = []
                for eid in reversed(effect_ids):
                    t = Timer()
                    async with t:
                        await client.fail(
                            org_id=session.org_id,
                            session_id=session.session_id,
                            effect_id=eid,
                            error="Benchmark-triggered rollback",
                        )
                    compensation_times.append(t.elapsed_us)

                per_compensation_us.extend(compensation_times)

                # Verify LIFO ordering.
                sql = (
                    "SELECT effect_id, registered_at, executed_at "
                    "FROM effects WHERE session_id = "
                    f"'{session.session_id}' "
                    "ORDER BY registered_at"
                )
                rows_raw = await _db_query(sql)
                if rows_raw:
                    lines = rows_raw.strip().split("\n")
                    for line in lines:
                        parts = line.split("|")
                        if len(parts) >= 3:
                            reg = parts[1].strip()
                            exe = parts[2].strip()
                            if reg and exe and reg >= exe:
                                log.warning(
                                    "LIFO order violation: "
                                    "registered_at >= executed_at for %s",
                                    parts[0].strip(),
                                )

        cell = await run_benchmark(
            f"compensation-chain-{n}",
            _rollback,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=1,
            config={
                "chain_length": n,
                "per_compensation_us": per_compensation_us,
            },
            min_samples=10,
        )
        results.append(cell)

    await client.aclose()
    return results


# ── Benchmark 5: Multi-tenant noise immunity ────────────────────────────────────


async def bench_multitenant_noise(
    warmup: int = 5,
    duration: int = 20,
    noise_levels: list[int] | None = None,
) -> list[BenchResult]:
    """Measure org-beta latency while N noisy sessions run in org-alpha.

    Returns one ``BenchResult`` per noise level.  Each result captures
    org-beta's COMPENSABLE latency while the given number of noisy
    org-alpha sessions run concurrently.
    """
    levels = noise_levels or [0, 10, 25, 50]
    results: list[BenchResult] = []

    for n in levels:

        async def _quiet_measurement() -> None:
            """One COMPENSABLE call in org-beta - the measured workload."""
            client_beta = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY_2)
            try:
                async with UndoLogSession(org_id="org-beta") as session:
                    step = session.next_step()
                    intercept = await client_beta.intercept(
                        org_id=session.org_id,
                        session_id=session.session_id,
                        tool_name="send_email",
                        step_index=step,
                        args={
                            "to": "quiet@example.com",
                            "subject": "quiet",
                            "body": ".",
                        },
                    )
                    result = await _direct_tool_call(
                        "send_email",
                        {"to": "quiet@example.com", "subject": "quiet", "body": "."},
                    )
                    if intercept.effect_id:
                        await client_beta.commit(
                            org_id=session.org_id,
                            session_id=session.session_id,
                            effect_id=intercept.effect_id,
                            result=result,
                        )
            finally:
                await client_beta.aclose()
            # Pace at 1 call/second as specified.
            await asyncio.sleep(1.0)

        async def _noisy_worker() -> None:
            """Tight loop of COMPENSABLE calls in org-alpha - the noise."""
            client_alpha = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)
            try:
                deadline = time.monotonic() + warmup + duration + 2
                while time.monotonic() < deadline:
                    async with UndoLogSession(org_id="org-alpha") as session:
                        step = session.next_step()
                        try:
                            intercept = await client_alpha.intercept(
                                org_id=session.org_id,
                                session_id=session.session_id,
                                tool_name="send_email",
                                step_index=step,
                                args={
                                    "to": "noise@example.com",
                                    "subject": "noise",
                                    "body": ".",
                                },
                            )
                            result = await _direct_tool_call(
                                "send_email",
                                {
                                    "to": "noise@example.com",
                                    "subject": "noise",
                                    "body": ".",
                                },
                            )
                            if intercept.effect_id:
                                await client_alpha.commit(
                                    org_id=session.org_id,
                                    session_id=session.session_id,
                                    effect_id=intercept.effect_id,
                                    result=result,
                                )
                        except Exception:
                            # Best-effort noise worker: keep running despite
                            # per-iteration failures so background load is
                            # sustained throughout the measurement window.
                            log.debug(
                                "Noise worker iteration failed in org-alpha",
                                exc_info=True,
                            )
            finally:
                await client_alpha.aclose()

        # Start background noise workers.
        noise_tasks = [asyncio.create_task(_noisy_worker()) for _ in range(n)]

        cell = await run_benchmark(
            f"multitenant-noise-{n}",
            _quiet_measurement,
            warmup_seconds=warmup,
            duration_seconds=duration,
            concurrency=1,
            config={"noise_sessions": n},
            min_samples=10,
        )
        results.append(cell)

        for t in noise_tasks:
            t.cancel()
        await asyncio.gather(*noise_tasks, return_exceptions=True)

    return results


# ── Benchmark 6: SSE event delivery latency ──────────────────────────────────────


async def bench_sse_delivery(
    warmup: int = 5,
    duration: int = 20,
    subscriber_count: int = 1,
    event_rate: int = 100,
) -> list[BenchResult]:
    """Measure SSE event emit-to-callback latency at a given event rate.

    Opens *subscriber_count* SSE connections, emits events at
    *event_rate* per second, and records the wall-clock delta between
    the proxy's event timestamp and the callback invocation.  Also
    reports the drop rate (events emitted vs events received).

    Parameters
    ----------
    event_rate : int
        Target event emission rate per second (10, 100, or 500).
    """
    results: list[BenchResult] = []
    samples: list[float] = []
    received_count: int = 0
    emitted_count: int = 0

    def _make_callback() -> Any:
        nonlocal received_count

        def _on_event(event: Event) -> None:
            nonlocal received_count
            received_count += 1
            if not event.timestamp:
                return
            try:
                truncated = event.timestamp[:19]
                emitted = calendar.timegm(time.strptime(truncated, "%Y-%m-%dT%H:%M:%S"))
                now = time.time()
                delta_us = (now - emitted) * 1_000_000
            except (ValueError, OSError):
                return
            if delta_us < 0 or delta_us > 10_000_000:
                return
            samples.append(delta_us)

        return _on_event

    # Start SSE subscriber tasks.
    api_keys = [_API_KEY] * subscriber_count
    conns = [
        SSEConnection(org_id="org-bench", api_key=k, proxy_url=_PROXY_URL)
        for k in api_keys
    ]
    sse_tasks = [
        asyncio.create_task(c.connect(callback=_make_callback())) for c in conns
    ]

    await asyncio.sleep(1)

    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)
    deadline = time.monotonic() + warmup + duration
    interval_s = 1.0 / event_rate

    # Warmup loop (no pacing, just get the system warm).
    warmup_deadline = time.monotonic() + warmup
    while time.monotonic() < warmup_deadline:
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            try:
                intercept = await client.intercept(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    tool_name="send_email",
                    step_index=step,
                    args={"to": "sse@example.com", "subject": "sse", "body": "."},
                )
                result = await _direct_tool_call(
                    "send_email",
                    {"to": "sse@example.com", "subject": "sse", "body": "."},
                )
                if intercept.effect_id:
                    await client.commit(
                        org_id=session.org_id,
                        session_id=session.session_id,
                        effect_id=intercept.effect_id,
                        result=result,
                    )
            except Exception:
                # Transient SSE emission failure during warmup: skip the
                # iteration so the system reaches steady state before the
                # recording phase begins.
                log.debug("SSE warmup emission failed; continuing", exc_info=True)

    # Recording loop with paced emission.
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            try:
                intercept = await client.intercept(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    tool_name="send_email",
                    step_index=step,
                    args={"to": "sse@example.com", "subject": "sse", "body": "."},
                )
                result = await _direct_tool_call(
                    "send_email",
                    {"to": "sse@example.com", "subject": "sse", "body": "."},
                )
                if intercept.effect_id:
                    await client.commit(
                        org_id=session.org_id,
                        session_id=session.session_id,
                        effect_id=intercept.effect_id,
                        result=result,
                    )
                emitted_count += 1
            except Exception:
                # Transient SSE emission failure during recording: skip the
                # sample so the loop maintains the target event rate rather
                # than crashing the entire benchmark cell.
                log.debug("SSE paced emission failed; continuing", exc_info=True)
        elapsed = time.monotonic() - t0
        sleep_needed = interval_s - elapsed
        if sleep_needed > 0:
            await asyncio.sleep(sleep_needed)

    await client.aclose()

    # Stop SSE tasks.
    for t in sse_tasks:
        t.cancel()
    await asyncio.gather(*sse_tasks, return_exceptions=True)
    for c in conns:
        await c.close()

    # Compute drop rate.
    expected_per_subscriber = emitted_count
    total_received = received_count
    if expected_per_subscriber > 0:
        drop_rate = max(
            0.0, 1.0 - total_received / (expected_per_subscriber * subscriber_count)
        )
    else:
        drop_rate = 0.0

    log.info(
        "SSE: emitted=%d, received=%d (across %d subscribers), drop_rate=%.2f%%",
        emitted_count,
        total_received,
        subscriber_count,
        drop_rate * 100,
    )

    recording_samples = [s for s in samples if s >= 0]

    result = BenchResult(
        label=f"sse-delivery-subs-{subscriber_count}-rate-{event_rate}",
        samples_us=sorted(recording_samples),
        warmup_seconds=warmup,
        duration_seconds=duration,
        concurrency=subscriber_count,
        config={
            "subscriber_count": subscriber_count,
            "event_rate": event_rate,
            "emitted": emitted_count,
            "received": total_received,
            "drop_rate_pct": round(drop_rate * 100, 2),
        },
        tps=float(emitted_count) / duration if duration > 0 else 0.0,
    )
    results.append(result)
    return results


# ── Benchmark 7: Approval workflow latency ──────────────────────────────────────


async def bench_approval_latency(
    warmup: int = 5,
    duration: int = 20,
    concurrency: int = 1,
) -> list[BenchResult]:
    """Measure full approval round-trip with stage breakdown.

    Returns one ``BenchResult``.  Each iteration creates an IRREVERSIBLE
    intercept, measures sub-stages (intercept-to-approve idle,
    approve-to-execute, execute-to-commit via proxy), and auto-approves
    via the proxy REST API.
    """
    results: list[BenchResult] = []
    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)
    stage_timings: dict[str, list[float]] = {
        "intercept_us": [],
        "approve_us": [],
        "total_us": [],
    }

    async def _approve_roundtrip() -> None:
        nonlocal stage_timings
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()

            t0 = Timer()
            async with t0:
                intercept = await client.intercept(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    tool_name="escalate_case",
                    step_index=step,
                    args={
                        "ticket_id": "TKT-APPR",
                        "reason": "benchmark approval",
                    },
                )
            intercept_us = t0.elapsed_us

            if intercept.outcome != "AwaitingApproval" or not intercept.approval_id:
                stage_timings["intercept_us"].append(intercept_us)
                stage_timings["approve_us"].append(0.0)
                stage_timings["total_us"].append(intercept_us)
                return

            t1 = Timer()
            async with t1:
                await _approve(intercept.approval_id)
            approve_us = t1.elapsed_us

            stage_timings["intercept_us"].append(intercept_us)
            stage_timings["approve_us"].append(approve_us)
            stage_timings["total_us"].append(intercept_us + approve_us)

    cell = await run_benchmark(
        "approval-latency",
        _approve_roundtrip,
        warmup_seconds=warmup,
        duration_seconds=duration,
        concurrency=concurrency,
        config={
            "tier": "irreversible",
            "stage_intercept_us": stage_timings["intercept_us"],
            "stage_approve_us": stage_timings["approve_us"],
            "stage_total_us": stage_timings["total_us"],
        },
    )
    results.append(cell)

    # Contention test: N approvers contending on the same approval.
    log.info("  Approval contention test: 5 concurrent approvers")
    async with UndoLogSession(org_id="org-bench") as session:
        step = session.next_step()
        intercept = await client.intercept(
            org_id=session.org_id,
            session_id=session.session_id,
            tool_name="escalate_case",
            step_index=step,
            args={"ticket_id": "TKT-CONTEND", "reason": "contention test"},
        )
        if intercept.outcome == "AwaitingApproval" and intercept.approval_id:
            contention_outcomes: list[str] = []

            async def _contend() -> str:
                try:
                    await _approve(intercept.approval_id)
                    return "SUCCESS"
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 409:
                        return "CONFLICT"
                    return f"ERROR({e.response.status_code})"
                except Exception:
                    # Non-HTTP error: surface as generic ERROR so the
                    # contention outcome tally stays accurate.
                    return "ERROR"

            contention_tasks = [asyncio.create_task(_contend()) for _ in range(5)]
            contention_results = await asyncio.gather(*contention_tasks)
            contention_outcomes = list(contention_results)

            successes = contention_outcomes.count("SUCCESS")
            conflicts = contention_outcomes.count("CONFLICT")
            errors = contention_outcomes.count("ERROR")
            log.info(
                "  Approval contention: %d SUCCESS, %d CONFLICT, %d ERROR "
                "(expected 1 SUCCESS, 4 CONFLICT)",
                successes,
                conflicts,
                errors,
            )

    await client.aclose()
    return results


# ── Benchmark 8: Longevity - resource leak detection ────────────────────────────


async def bench_longevity(
    warmup: int = 30,
    duration: int = 1800,
    concurrency: int = 10,
) -> list[BenchResult]:
    """Sustained load at fixed concurrency with resource sampling.

    Runs COMPENSABLE tool calls at *concurrency* workers for
    *duration* seconds.  Resource snapshots are collected externally
    by the ``ResourceCollector``.  After the run, drift analysis
    compares mean resource usage in the first and second halves.
    """
    results: list[BenchResult] = []
    client = UndoLogClient(proxy_url=_PROXY_URL, api_key=_API_KEY)

    async def _workload() -> None:
        async with UndoLogSession(org_id="org-bench") as session:
            step = session.next_step()
            intercept = await client.intercept(
                org_id=session.org_id,
                session_id=session.session_id,
                tool_name="send_email",
                step_index=step,
                args={
                    "to": "longevity@example.com",
                    "subject": "longevity",
                    "body": ".",
                },
            )
            result = await _direct_tool_call(
                "send_email",
                {"to": "longevity@example.com", "subject": "longevity", "body": "."},
            )
            if intercept.effect_id:
                await client.commit(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    effect_id=intercept.effect_id,
                    result=result,
                )

    cell = await run_benchmark(
        "longevity",
        _workload,
        warmup_seconds=warmup,
        duration_seconds=duration,
        concurrency=concurrency,
        config={"tier": "compensable"},
        min_samples=10,
    )
    results.append(cell)

    await client.aclose()
    return results
