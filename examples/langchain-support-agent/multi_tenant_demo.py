"""Multi-tenant demo : concurrent agents in isolated orgs with SSE observability.

This demo runs two agents concurrently in different organisations against the
same UndoLog stack, with sessions, effects, approvals, and SSE events
scoped by org.

Workflow
--------
*Org-alpha (dev-key)*:
    SAFE lookup_customer -> COMPENSABLE send_email ->
    IRREVERSIBLE escalate_case (AwaitingApprovalError) -> auto-approve

*Org-beta (dev-key-2)*:
    SAFE lookup_customer -> COMPENSABLE send_email ->
    COMPENSABLE create_ticket (forced failure) -> compensation rollback

Throughout execution an SSE dashboard consumer subscribes to both org streams
and renders every lifecycle event to the console in real time, making the
org isolation visible.

Prerequisites
-------------
*   UndoLog stack running (postgres + engine + proxy + tool-server).
*   Migration 0004 applied (second demo org).
*   Proxy configured with both API keys (see docker-compose.yml).
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
from undolog_sdk.client import UndoLogClient
from undolog_sdk.session import UndoLogSession

_examples_root = os.path.join(os.path.dirname(__file__), "..")
if _examples_root not in sys.path:
    sys.path.insert(0, _examples_root)

from example_tools import get_tool_registry  # noqa: E402

from sse_dashboard import Event, SSEConnection  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("multi_tenant_demo")

_PROXY_URL = os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


def _tool_server_url() -> str:
    return os.environ.get("MOCK_TOOL_SERVER_URL", "http://localhost:9091")


async def _call_mock_tool(tool_name: str, args: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_tool_server_url()}/tools",
            json={"tool_name": tool_name, "args": args},
            timeout=5.0,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["output"])


def _step(label: str) -> None:
    log.info("")
    log.info("─" * 60)
    log.info("  %s", label)
    log.info("─" * 60)


async def _run_org_alpha() -> None:
    """Run a workflow in org-alpha: SAFE, COMPENSABLE, IRREVERSIBLE + approve.

    Uses the tool registry (``@undolog_tool`` decorator) and the module-level
    default client which authenticates with ``dev-key``.
    """
    tools = get_tool_registry()

    async with UndoLogSession(org_id="org-alpha") as session:
        log.info("[org-alpha] Session: %s", session.session_id)
        log.info("")

        # SAFE : bypasses proxy, no effect logged.
        cust = await tools["lookup_customer"](customer_id="cust_101", _session=session)
        log.info("[org-alpha] lookup_customer -> %s", json.dumps(cust))

        # COMPENSABLE : proxy intercepts, effect committed.
        email = await tools["send_email"](
            to="alpha@example.com",
            subject="Your org-alpha ticket",
            body="We received your request.",
            _session=session,
        )
        log.info("[org-alpha] send_email -> %s", json.dumps(email))

        # IRREVERSIBLE : creates pending approval.
        try:
            await tools["escalate_case"](
                ticket_id="TKT-001",
                reason="VIP customer needs priority handling",
                _session=session,
            )
            log.warning("[org-alpha] escalate_case returned without AwaitingApprovalError")
            return
        except AwaitingApprovalError as exc:
            log.info("[org-alpha] AwaitingApprovalError: approval_id=%s", exc.approval_id)
            approval_id: str | None = exc.approval_id

        # Auto-approve via proxy API.
        if approval_id:
            await asyncio.sleep(1)
            url = f"{_PROXY_URL}/approvals/{approval_id}/approve"
            headers = {
                "X-Api-Key": "dev-key",
                "Content-Type": "application/json",
            }
            body = {"actor": "multi_tenant_demo", "note": "Auto-approved"}
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                result = resp.json()
                log.info("[org-alpha] Approve response: %s", json.dumps(result, indent=2))

        log.info("[org-alpha] Workflow complete")


async def _run_org_beta() -> None:
    """Run a workflow in org-beta: SAFE, COMPENSABLE, COMPENSABLE + forced failure.

    Uses ``UndoLogClient`` directly with the ``dev-key-2`` API key.
    """
    client = UndoLogClient(
        proxy_url=_PROXY_URL,
        api_key="dev-key-2",
    )

    async with UndoLogSession(org_id="org-beta") as session:
        log.info("[org-beta]  Session: %s", session.session_id)
        log.info("")

        # SAFE : call lookup_customer directly (no proxy).
        cust = await _call_mock_tool("lookup_customer", {"customer_id": "cust_202"})
        log.info("[org-beta]  lookup_customer -> %s", json.dumps(cust))

        # COMPENSABLE #1 : send_email.
        step = session.next_step()
        intercept = await client.intercept(
            org_id=session.org_id,
            session_id=session.session_id,
            tool_name="send_email",
            step_index=step,
            args={
                "to": "beta@example.com",
                "subject": "Your org-beta ticket",
                "body": "We are on it.",
            },
        )
        log.info("[org-beta]  Intercept outcome: %s", intercept.outcome)
        result = await _call_mock_tool(
            "send_email",
            {"to": "beta@example.com", "subject": "Your org-beta ticket", "body": "We are on it."},
        )
        if intercept.effect_id:
            await client.commit(
                org_id=session.org_id,
                session_id=session.session_id,
                effect_id=intercept.effect_id,
                result=result,
            )
        log.info("[org-beta]  send_email committed")

        # COMPENSABLE #2 : create_ticket with forced failure.
        step = session.next_step()
        intercept2 = await client.intercept(
            org_id=session.org_id,
            session_id=session.session_id,
            tool_name="create_ticket",
            step_index=step,
            args={
                "customer_id": "cust_202",
                "priority": "high",
                "description": "Dashboard login failure",
            },
        )
        log.info("[org-beta]  Intercept outcome: %s", intercept2.outcome)
        if intercept2.effect_id:
            await client.fail(
                org_id=session.org_id,
                session_id=session.session_id,
                effect_id=intercept2.effect_id,
                error="Downstream ticket service unreachable",
            )
        log.info("[org-beta]  create_ticket failed -> compensation rollback triggered")
        log.info("[org-beta]  Workflow complete")

    await client.aclose()


def _short_id(raw: str) -> str:
    """Truncate a UUID to 8 characters for more compact display."""
    return raw.split("-")[0] if "-" in raw else raw[:8]


def _format_event(label: str, event: Event) -> str:
    """Format one SSE event into a compact one-line string with action IDs."""
    icon = {
        "effect_intercepted": "  >",
        "effect_executed": "  >",
        "effect_committed": "  OK",
        "effect_replayed": "  >>",
        "effect_failed": "  ERR",
        "approval_required": "  !",
        "approval_approved": "  YES",
        "approval_rejected": "  NO",
    }.get(event.type, "  ?")
    ts = event.timestamp[11:23] if len(event.timestamp) > 23 else event.timestamp
    parts = [f"  [{label}]", f"[{ts}]", icon, event.type]
    if event.session_id:
        parts.append(f"sess={_short_id(event.session_id)}")
    if event.effect_id:
        parts.append(f"eff={_short_id(event.effect_id)}")
    if event.approval_id:
        parts.append(f"appr={_short_id(event.approval_id)}")
    return " ".join(parts)


def _sse_callback_alpha(event: Event) -> None:
    """Display SSE events for org-alpha with a distinct label."""
    print(_format_event("alpha", event))


def _sse_callback_beta(event: Event) -> None:
    """Display SSE events for org-beta with a distinct label."""
    print(_format_event("beta", event))


async def main() -> None:
    """Run the multi-tenant demo with concurrent agents and SSE observability."""
    log.info("UndoLog : Multi-Tenant Demo with SSE Observability")
    log.info("─" * 60)
    log.info("Proxy: %s", _PROXY_URL)
    log.info("")
    log.info("  org-alpha  (dev-key)    : SAFE -> COMPENSABLE -> IRREVERSIBLE + approve")
    log.info("  org-beta   (dev-key-2)  : SAFE -> COMPENSABLE -> COMPENSABLE + failure")
    log.info("")

    # Start SSE subscribers in the background for both orgs.
    conn_alpha = SSEConnection(
        org_id="org-alpha",
        api_key="dev-key",
        proxy_url=_PROXY_URL,
    )
    conn_beta = SSEConnection(
        org_id="org-beta",
        api_key="dev-key-2",
        proxy_url=_PROXY_URL,
    )

    sse_task_alpha = asyncio.create_task(
        conn_alpha.connect(callback=_sse_callback_alpha),
        name="sse-alpha",
    )
    sse_task_beta = asyncio.create_task(
        conn_beta.connect(callback=_sse_callback_beta),
        name="sse-beta",
    )

    # Short pause for SSE connections to establish.
    await asyncio.sleep(1)

    # Run both agents concurrently.
    try:
        await asyncio.gather(
            _run_org_alpha(),
            _run_org_beta(),
        )
    except Exception as exc:
        log.error("Demo workflow error: %s", exc)

    # Verify org isolation by querying each org's approvals.
    _step("Verification : org isolation check")
    async with httpx.AsyncClient() as client:
        for org_label, api_key in [("org-alpha", "dev-key"), ("org-beta", "dev-key-2")]:
            resp = await client.get(
                f"{_PROXY_URL}/approvals",
                headers={"X-Api-Key": api_key},
            )
            resp.raise_for_status()
            approvals = resp.json()
            log.info(
                "  %s : %d approval(s)",
                org_label,
                len(approvals) if isinstance(approvals, list) else 0,
            )
    log.info("  (Each org sees only its own approvals)")

    log.info("")
    log.info("─" * 60)
    log.info("  Demo complete : multi-tenant isolation verified")
    log.info("─" * 60)

    # Cancel SSE tasks.
    sse_task_alpha.cancel()
    sse_task_beta.cancel()
    await asyncio.gather(sse_task_alpha, sse_task_beta, return_exceptions=True)

    await conn_alpha.close()
    await conn_beta.close()


if __name__ == "__main__":
    asyncio.run(main())
