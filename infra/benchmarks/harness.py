"""Async benchmark harness with warmup, steady-state detection, and timing.

Usage::

    results = await run_benchmark(
        label="overhead-compensable",
        factory=lambda: some_async_fn(),
        warmup_seconds=5,
        duration_seconds=30,
    )
    print(results.summary())
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from statistics import quantiles, stdev
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class BenchResult:
    """Aggregated timing results for one benchmark cell.

    Stores raw samples and computes percentiles on access.
    """

    label: str
    """Human-readable label for this cell (e.g. ``overhead-compensable``)."""
    samples_us: list[float] = field(default_factory=list)
    """Raw latency samples in microseconds."""

    warmup_seconds: int = 0
    duration_seconds: int = 0
    concurrency: int = 1
    config: dict[str, Any] = field(default_factory=dict)
    steady_state_ok: bool = True
    tps: float = 0.0

    @property
    def count(self) -> int:
        return len(self.samples_us)

    @property
    def min_us(self) -> float:
        return min(self.samples_us) if self.samples_us else 0.0

    @property
    def max_us(self) -> float:
        return max(self.samples_us) if self.samples_us else 0.0

    @property
    def mean_us(self) -> float:
        return (sum(self.samples_us) / len(self.samples_us)) if self.samples_us else 0.0

    @property
    def stddev_us(self) -> float:
        return stdev(self.samples_us) if len(self.samples_us) >= 2 else 0.0

    @property
    def p50_us(self) -> float:
        return self._percentile(50)

    @property
    def p95_us(self) -> float:
        return self._percentile(95)

    @property
    def p99_us(self) -> float:
        return self._percentile(99)

    def _percentile(self, pct: int) -> float:
        if len(self.samples_us) < 2:
            return self.samples_us[0] if self.samples_us else 0.0
        q = quantiles(self.samples_us, n=100)
        return q[pct - 1]

    def summary(self) -> str:
        """Return a human-readable one-line summary."""
        tps_str = f"tps={self.tps:>8.1f}" if self.tps else ""
        ss_flag = "" if self.steady_state_ok else " !SS!"
        return (
            f"{self.label:<40s} "
            f"n={self.count:<6d} "
            f"p50={self.p50_us:>8.1f}us "
            f"p95={self.p95_us:>8.1f}us "
            f"p99={self.p99_us:>8.1f}us "
            f"mean={self.mean_us:>8.1f}us "
            f"std={self.stddev_us:>8.1f}us "
            f"min={self.min_us:>8.1f}us "
            f"max={self.max_us:>8.1f}us"
            f"{tps_str}"
            f"{ss_flag}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        d: dict[str, Any] = {
            "benchmark": self.label,
            "samples": self.count,
            "warmup_seconds": self.warmup_seconds,
            "duration_seconds": self.duration_seconds,
            "concurrency": self.concurrency,
            "config": self.config,
            "steady_state_ok": self.steady_state_ok,
            "latency_us": {
                "min": round(self.min_us, 1),
                "p50": round(self.p50_us, 1),
                "p95": round(self.p95_us, 1),
                "p99": round(self.p99_us, 1),
                "max": round(self.max_us, 1),
                "mean": round(self.mean_us, 1),
                "stddev": round(self.stddev_us, 1),
            },
        }
        if self.tps:
            d["tps"] = round(self.tps, 1)
        return d


class Timer:
    """Async context manager that records elapsed wall-clock time.

    Usage::

        timer = Timer()
        async with timer:
            await some_operation()
        print(timer.elapsed_us)
    """

    def __init__(self) -> None:
        self._start: float | None = None
        self.elapsed_us: float = 0.0

    async def __aenter__(self) -> Timer:
        self._start = time.monotonic()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._start is not None:
            self.elapsed_us = (time.monotonic() - self._start) * 1_000_000


async def run_benchmark(
    label: str,
    factory: Any,
    *,
    warmup_seconds: int = 3,
    duration_seconds: int = 10,
    concurrency: int = 1,
    config: dict[str, Any] | None = None,
    min_samples: int = 1000,
) -> BenchResult:
    """Run *factory* in a loop with warmup and steady-state recording.

    Parameters
    ----------
    label : str
        Unique label for the result set.
    factory : async callable
        Called once per iteration.  Must return when the operation is
        complete so the loop can record the latency.
    warmup_seconds : int
        How long (in wall-clock seconds) to run the operation before
        starting to record samples.
    duration_seconds : int
        How long (in wall-clock seconds) to record samples.
    concurrency : int
        Number of concurrent workers. Each worker runs the factory in a
        tight loop and records its own samples.
    config : dict or None
        Arbitrary metadata attached to the result.
    min_samples : int
        Minimum number of samples required. A warning is logged when
        the actual count falls below this threshold.

    Returns
    -------
    BenchResult
        Aggregated timing data from all workers.
    """
    result = BenchResult(
        label=label,
        warmup_seconds=warmup_seconds,
        duration_seconds=duration_seconds,
        concurrency=concurrency,
        config=config or {},
    )
    # Timestamped samples for steady-state detection.
    timestamped: list[tuple[float, float]] = []
    lock: asyncio.Lock = asyncio.Lock()

    async def _worker(worker_id: int) -> None:
        nonlocal timestamped
        local_ts: list[tuple[float, float]] = []
        deadline = time.monotonic() + warmup_seconds + duration_seconds
        warmup_deadline = time.monotonic() + warmup_seconds

        while time.monotonic() < deadline:
            timer = Timer()
            async with timer:
                await factory()
            now = time.monotonic()
            if now >= warmup_deadline:
                local_ts.append((now, timer.elapsed_us))

        async with lock:
            timestamped.extend(local_ts)

    workers = [asyncio.create_task(_worker(i)) for i in range(concurrency)]
    await asyncio.gather(*workers)

    # Sort by recording time and extract latencies.
    timestamped.sort(key=lambda x: x[0])
    samples = [s[1] for s in timestamped]

    result.samples_us = sorted(samples)
    result.tps = result.count / duration_seconds if duration_seconds > 0 else 0.0

    # Steady-state check: split recording window in half by time.
    midpoint = len(timestamped) // 2
    if midpoint > 0:
        first_half_mean = sum(s[1] for s in timestamped[:midpoint]) / midpoint
        second_half_mean = sum(s[1] for s in timestamped[midpoint:]) / (
            len(timestamped) - midpoint
        )
        if first_half_mean > 0:
            diff_pct = abs(second_half_mean - first_half_mean) / first_half_mean * 100
            if diff_pct > 10:
                log.warning(
                    "Steady-state check FAILED for '%s': "
                    "second-half mean %.1fus differs from first-half "
                    "mean %.1fus by %.1f%% (threshold 10%%)",
                    label,
                    second_half_mean,
                    first_half_mean,
                    diff_pct,
                )
                result.steady_state_ok = False
            else:
                log.info(
                    "Steady-state check PASSED for '%s': %.1f%% drift",
                    label,
                    diff_pct,
                )

    # Minimum sample check.
    if result.count < min_samples:
        log.warning(
            "Only %d samples for '%s' (minimum %d recommended for stable p99)",
            result.count,
            label,
            min_samples,
        )

    return result
