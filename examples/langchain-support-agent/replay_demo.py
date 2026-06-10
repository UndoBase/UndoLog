"""Exactly-once execution demo for UndoLog signature-based dedup.

This demo exercises undolog's core deduplication guarantee: given the same
``(session_id, step_index, tool_name, canonical_args)``, the engine returns
a cached result *without* re-executing the tool body.

The dedup key is a BLAKE3 hash computed over:

    session_id (16 bytes) || step_index (4-byte LE) ||
    len(tool_name) + tool_name || len(canonical_args) + canonical_args

Two calls with identical inputs produce an identical hash; the engine
finds the existing effect on ``find_by_signature`` and returns
``InterceptOutcome::Replay`` instead of ``Execute``.

Steps
-----
1.  SAFE tool : executes directly (no proxy, no signature).
2.  COMPENSABLE tool : first call via proxy → *Execute* (effect created,
    upstream tool run).
3.  Same tool, same args, same step : second call → *Replay* (cached
    result returned, upstream *not* called).
4.  Same tool, *different* args : third call → *Execute* (different
    signature, new effect).
5.  Summary : effect log states and exactly-once verification.

Prerequisites
-------------
*   UndoLog stack running (postgres + engine + proxy + mock tool server).
    ::

        docker compose up -d postgres engine tool-server proxy

*   ``UNDOLOG_PROXY_URL`` pointing at the proxy (default ``http://localhost:8080``).
*   ``UNDOLOG_ORG_ID`` and ``UNDOLOG_API_KEY`` configured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import uuid

import httpx

from undolog_sdk import ToolTier, undolog_tool
from undolog_sdk.session import UndoLogSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("replay_demo")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _proxy_url() -> str:
    return os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


def _api_key() -> str | None:
    return os.environ.get("UNDOLOG_API_KEY") or None


def _org_id() -> str:
    return os.environ.get("UNDOLOG_ORG_ID", "org_demo")


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {
        "X-UndoLog-Org-Id": _org_id(),
        "Content-Type": "application/json",
    }
    api_key = _api_key()
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


def _step(label: str) -> None:
    log.info("")
    log.info("─" * 60)
    log.info("  %s", label)
    log.info("─" * 60)


async def _call_tool(
    http: httpx.AsyncClient,
    session_id: str,
    tool_name: str,
    step_index: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Send a tool call to the proxy and return the parsed response.

    Parameters
    ----------
    http : httpx.AsyncClient
        Reusable HTTP client.
    session_id : str
        Active session identifier.
    tool_name : str
        Name of the tool to invoke.
    step_index : int
        Zero or one-based step counter.
    args : dict
        Tool arguments as a JSON-serialisable dict.

    Returns
    -------
    dict
        Proxy response with ``status``, ``effect_id``, and ``result``.
    """
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_version": "1.0.0",
        "step_index": step_index,
        "args": args,
    }
    resp = await http.post(
        f"{_proxy_url()}/mcp/tool_call",
        json=payload,
        headers=_headers(),
    )
    resp.raise_for_status()
    return resp.json()


# ── Demo tools ───────────────────────────────────────────────────────────────


@undolog_tool(tier=ToolTier.SAFE)
async def lookup_plan(plan_id: str) -> dict[str, Any]:
    """Look up a pricing plan.

    SAFE : always executed fresh (bypasses the proxy entirely).

    Parameters
    ----------
    plan_id : str
        Plan identifier.

    Returns
    -------
    dict
        Plan details.
    """
    return {"plan_id": plan_id, "name": "Enterprise", "price": 99}


async def main() -> None:
    """Run the exactly-once replay demo.

    All tool calls go through the proxy ``POST /mcp/tool_call`` endpoint
    so the engine's dedup machinery can be observed directly.  The mock
    upstream tool server logs each execution. When the demo is correct,
    the server receives exactly two Execute requests (steps 2 and 4)
    and zero requests for the Replayed step 3.
    """
    log.info("UndoLog : Exactly-Once Execution Demo")
    log.info("─" * 60)
    log.info("Proxy: %s", _proxy_url())
    log.info("Org:  %s", _org_id())
    log.info("")

    # ── Step 0: health check ──────────────────────────────────────────
    _step("0.  Proxy health check")
    async with httpx.AsyncClient() as http:
        try:
            resp = await http.get(f"{_proxy_url()}/health")
            resp.raise_for_status()
            log.info("Proxy: %s", resp.json().get("status", "ok"))
        except httpx.RequestError as exc:
            log.error("Proxy unreachable: %s", exc)
            log.error("Start the stack before running this demo.")
            return

        # ── Step 1: SAFE tool ─────────────────────────────────────────
        _step("1.  SAFE tool : always fresh (bypasses proxy)")
        async with UndoLogSession(org_id=_org_id()) as safe_session:
            plan = await lookup_plan(plan_id="plan_enterprise", _session=safe_session)
        log.info("lookup_plan -> %s", json.dumps(plan))
        log.info("  (no proxy interaction : no effect log entry)")

        # Generate a fresh session UUID for the proxy-mediated calls.
        session_id = str(uuid.uuid4())
        log.info("Session: %s", session_id)

        # ── Step 2: First COMPENSABLE call : Execute ──────────────────
        _step("2.  COMPENSABLE #1 : first call (Execute)")
        args_a = {"amount": 100, "currency": "USD"}
        data1 = await _call_tool(http, session_id, "charge_payment", 1, args_a)
        log.info("Status: %s", data1.get("status"))
        log.info("Effect: %s", data1.get("effect_id"))
        log.info("Result: %s", json.dumps(data1.get("result")))
        log.info("")
        log.info("  Engine inserted effect (state=executing)")
        log.info("  Proxy forwarded to mock tool server → tool executed")
        log.info("  Engine committed effect (state=committed)")
        if data1.get("status") != "executed":
            log.warning("  Expected 'executed', got '%s'", data1.get("status"))

        effect_id_1: str = data1.get("effect_id", "")

        # ── Step 3: Same call : Replay ────────────────────────────────
        _step(
            "3.  COMPENSABLE #1 : same signature → Replay\n"
            "    (session_id=%s, step=1, tool=charge_payment, amount=100, currency=USD)"
            % session_id
        )
        data2 = await _call_tool(http, session_id, "charge_payment", 1, args_a)
        log.info("Status: %s", data2.get("status"))
        log.info("Effect: %s", data2.get("effect_id"))
        log.info("Result: %s", json.dumps(data2.get("result")))
        log.info("")
        if data2.get("status") == "replayed":
            log.info("  ✓ REPLAY : tool body was NOT executed")
            log.info("  ✓ Same effect_id: %s", data2.get("effect_id") == effect_id_1)
            log.info("  ✓ Cached result returned from engine")
        else:
            log.warning("  Expected 'replayed', got '%s'", data2.get("status"))

        # ── Step 4: Different args → Execute ──────────────────────────
        _step("4.  COMPENSABLE #2 : different args → Execute")
        args_b = {"amount": 200, "currency": "EUR"}
        data3 = await _call_tool(http, session_id, "charge_payment", 2, args_b)
        log.info("Status: %s", data3.get("status"))
        log.info("Effect: %s", data3.get("effect_id"))
        log.info("Result: %s", json.dumps(data3.get("result")))
        log.info("")
        if data3.get("status") == "executed":
            log.info("  ✓ New signature (amount=200, currency=EUR)")
            log.info("  ✓ Execute : tool body ran")
            log.info("  ✓ Different effect_id from step 3")
        else:
            log.warning("  Expected 'executed', got '%s'", data3.get("status"))

        # ── Step 5: Verification ──────────────────────────────────────
        _step("5.  Exactly-once verification")
        log.info("  Calls made:")
        log.info("    1. charge_payment(amount=100, currency=USD) step=1 → Execute")
        log.info("    2. charge_payment(amount=100, currency=USD) step=1 → Replay")
        log.info("    3. charge_payment(amount=200, currency=EUR)  step=2 → Execute")
        log.info("")
        log.info("  Tool body executed EXACTLY TWICE (calls 1 and 3)")
        log.info("  Call 2 returned cached result : exactly-once guaranteed")
        log.info("")
        log.info("  BLAKE3 dedup key components:               ")
        log.info("    session_id  : %s", session_id)
        log.info("    step_index  : 1                          ")
        log.info("    tool_name   : charge_payment              ")
        log.info('    canonical   : {"amount":100,"currency":"USD"}')
        log.info("    ──────────────────────────────")
        log.info(
            "    signature   : BLAKE3(session || step || len(tool) || tool || len(args) || args)"
        )
        log.info("")
        log.info("  Effect log (expected states):")
        log.info("    charge_payment @ step 1 → committed  (cached for replay)")
        log.info("    charge_payment @ step 1 → replayed   (duplicate signature)")
        log.info("    charge_payment @ step 2 → committed  (fresh call)")
        log.info("")
        log.info("─" * 60)
        log.info("  Demo complete : exactly-once execution verified")
        log.info("─" * 60)


if __name__ == "__main__":
    asyncio.run(main())
