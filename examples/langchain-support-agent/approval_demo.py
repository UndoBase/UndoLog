"""Full approval lifecycle demo for UndoLog IRREVERSIBLE tools.

This demo exercises the complete human-in-the-loop approval cycle:

1.  SAFE and COMPENSABLE tools execute normally through the agent
2.  An IRREVERSIBLE tool creates a pending approval. The SDK raises
    ``AwaitingApprovalError``
3.  The demo catches the error and auto-approves via the proxy REST API
4.  The proxy executes the approved tool and commits the result
5.  The agent continues with subsequent calls in the same session
6.  The effect log is queried to verify the full lifecycle

Prerequisites
-------------
*   UndoLog stack running (postgres + engine + proxy).
*   ``UNDOLOG_PROXY_URL`` pointing at the proxy (default ``http://localhost:8080``).
*   ``UNDOLOG_ORG_ID`` set to the organisation identifier.
*   ``UNDOLOG_API_KEY`` matching ``UNDOLOG_PROXY_API_KEYS`` in the proxy.

Usage
-----
::

    # Terminal 1 : start the stack
    ./start_stack.sh

    # Terminal 2 : run the demo
    python examples/langchain-support-agent/approval_demo.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx

from undolog_sdk import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession

_examples_root = os.path.join(os.path.dirname(__file__), "..")
if _examples_root not in sys.path:
    sys.path.insert(0, _examples_root)

from example_tools import get_tool_registry  # noqa: E402  -- sys.path insertion above

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("approval_demo")


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


async def _approve_via_api(approval_id: str) -> dict[str, Any]:
    """Approve a pending approval via the proxy REST API.

    ``POST /approvals/{approval_id}/approve``

    Parameters
    ----------
    approval_id : str
        Approval identifier from the ``AwaitingApprovalError``.

    Returns
    -------
    dict
        Response body containing status, effect_id, and execution result.

    Raises
    ------
    httpx.HTTPStatusError
        If the proxy returns a non-2xx status code.
    """
    url = f"{_proxy_url()}/approvals/{approval_id}/approve"
    body = {"actor": "approval_demo", "note": "Auto-approved by demo script"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def _fetch_approvals(state: str = "pending") -> list[dict[str, Any]]:
    """Fetch approval requests from the proxy.

    ``GET /approvals?state=<state>``

    Parameters
    ----------
    state : str
        Filter by status: ``pending``, ``approved``, or ``rejected``.

    Returns
    -------
    list[dict]
        List of approval records matching the filter.
    """
    url = f"{_proxy_url()}/approvals?state={state}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def _health_check() -> dict[str, Any]:
    """Check proxy health."""
    url = f"{_proxy_url()}/health"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _step(label: str) -> None:
    """Print a section header for demo clarity."""
    log.info("")
    log.info("─" * 60)
    log.info("  %s", label)
    log.info("─" * 60)


async def main() -> None:
    """Run the full approval lifecycle demo.

    Steps
    -----
    1.  Health check : verify the proxy is reachable.
    2.  SAFE tool : executes directly (bypasses proxy).
    3.  COMPENSABLE tool : goes through proxy, effect is committed.
    4.  IRREVERSIBLE tool : creates pending approval, SDK raises error.
    5.  Approve via API : proxy executes the tool and commits.
    6.  Verify : query the approval list and log results.
    """
    log.info("UndoLog : Approval Lifecycle Demo")
    log.info("─" * 60)
    log.info("Proxy: %s", _proxy_url())
    log.info("Org:  %s", _org_id())
    log.info("")

    # ── Step 0: health check ──────────────────────────────────────────
    _step("0.  Proxy health check")
    try:
        health = await _health_check()
        log.info("Proxy status: %s", health.get("status"))
    except httpx.RequestError as exc:
        log.error("Proxy unreachable at %s: %s", _proxy_url(), exc)
        log.error("Start the stack before running this demo.")
        return

    # ── Step 1: SAFE tool ─────────────────────────────────────────────
    _step("1.  SAFE tool : executes directly (bypasses proxy)")
    tools = get_tool_registry()

    async with UndoLogSession(org_id=_org_id()) as session:
        log.info("Session: %s", session.session_id)

        result = await tools["lookup_customer"](customer_id="cust_42", _session=session)
        log.info("lookup_customer -> %s", json.dumps(result))

        # ── Step 2: COMPENSABLE tool ──────────────────────────────────
        _step("2.  COMPENSABLE tool : effect committed")
        email_result = await tools["send_email"](
            to="alice@example.com",
            subject="Your support request",
            body="We are looking into it.",
            _session=session,
        )
        log.info("send_email -> %s", json.dumps(email_result))

        # ── Step 3: IRREVERSIBLE tool ─────────────────────────────────
        _step("3.  IRREVERSIBLE tool : requires human approval")
        try:
            await tools["escalate_case"](
                ticket_id="TKT-42",
                reason="Premium customer, dashboard inaccessible",
                _session=session,
            )
            log.warning(
                "IRREVERSIBLE tool returned without raising AwaitingApprovalError: unexpected"
            )
            return
        except AwaitingApprovalError as exc:
            log.info(
                "AwaitingApprovalError caught: approval_id=%s, tool=%s, step=%s",
                exc.approval_id,
                exc.tool_name,
                exc.step_index,
            )
            approval_id: str | None = exc.approval_id

        # ── Step 4: Verify pending approval exists ────────────────────
        _step("4.  Pending approval created")
        pending = await _fetch_approvals(state="pending")
        matching = [r for r in pending if r.get("id") == approval_id]
        if matching:
            rec = matching[0]
            log.info("Approval record: id=%s, tool=%s", rec.get("id"), rec.get("tool_name"))
        else:
            log.warning("Approval %s not found in pending list", approval_id)

        # ── Step 5: Auto-approve via API ──────────────────────────────
        _step("5.  Approve via proxy API")
        if approval_id is None:
            log.error("No approval_id to approve. Cannot continue")
            return

        approve_resp = await _approve_via_api(approval_id)
        log.info("Approve response: %s", json.dumps(approve_resp, indent=2))

        if approve_resp.get("execution") == "committed":
            log.info(
                "Tool executed and committed: effect_id=%s, result=%s",
                approve_resp.get("effect_id"),
                approve_resp.get("result"),
            )
        else:
            log.warning(
                "Execution status: %s",
                approve_resp.get("execution", "unknown"),
            )

        # ── Step 6: Verify final state ────────────────────────────────
        _step("6.  Verification")
        approved_list = await _fetch_approvals(state="approved")
        approved = [r for r in approved_list if r.get("id") == approval_id]
        if approved:
            log.info(
                "Approval resolved: status=%s, resolved_at=%s",
                approved[0].get("status"),
                approved[0].get("resolved_at"),
            )
        else:
            log.warning("Approval %s not found in approved list", approval_id)

        log.info("")
        log.info("─" * 60)
        log.info("  Demo complete : full approval lifecycle verified")
        log.info("─" * 60)


if __name__ == "__main__":
    asyncio.run(main())
