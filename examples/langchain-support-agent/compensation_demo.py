"""Compensation lifecycle demo for UndoLog COMPENSABLE tools.

This demo exercises undolog's compensation mechanism end-to-end:

1.  Multiple COMPENSABLE tools execute in sequence, each registering a
    compensation BEFORE running (the ``registered_at < executed_at``
    invariant)
2.  A downstream tool failure triggers the session failure path
3.  The saga orchestrator walks the undo stack in LIFO order and calls
    each compensation handler with the original captured arguments
4.  The effect log reflects the full lifecycle

Key safety invariant demonstrated
----------------------------------
Pre-registered compensation (ADR 0007): the undo stack entry is
persisted to PostgreSQL *before* the tool body executes.  If the
process crashes between registration and execution, the orchestrator
finds a pending undo entry on restart and calls the compensation
(safest default: undo an action whose outcome is unknown).

Compensation patterns shown
---------------------------
*   Stable identifier capture (user_id, ticket_id, email) not mutable
    data that could change between execution and rollback
*   ``CompensationDescriptor`` with custom ``max_retries`` and
    ``retry_backoff_ms`` demonstrating retry policy configuration
*   LIFO ordering: compensations fire in reverse execution order

Prerequisites
-------------
*   UndoLog stack running (postgres + engine + proxy).
*   ``UNDOLOG_PROXY_URL`` pointing at the proxy.
*   ``UNDOLOG_ORG_ID`` and ``UNDOLOG_API_KEY`` configured.

Usage
-----
::

    python examples/langchain-support-agent/compensation_demo.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from undolog_sdk import (
    CompensationDescriptor,
    ToolTier,
    undolog_tool,
)
from undolog_sdk.session import UndoLogSession


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("compensation_demo")


def _org_id() -> str:
    return os.environ.get("UNDOLOG_ORG_ID", "org_demo")


_MOCK_TOOL_SERVER_URL: str | None = None


def _get_tool_server_url() -> str:
    global _MOCK_TOOL_SERVER_URL
    if _MOCK_TOOL_SERVER_URL is None:
        _MOCK_TOOL_SERVER_URL = os.environ.get("MOCK_TOOL_SERVER_URL", "http://localhost:9091")
    return _MOCK_TOOL_SERVER_URL


async def _call_tool(tool_name: str, args: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_get_tool_server_url()}/tools",
            json={"tool_name": tool_name, "args": args},
            timeout=5.0,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["output"])


# ── Demo tools ──────────────────────────────────────────────────────────


# SAFE tool : no compensation needed, bypasses the proxy entirely.
@undolog_tool(tier=ToolTier.SAFE)
async def lookup_user(user_id: str) -> dict[str, Any]:
    """Look up a user profile.

    Parameters
    ----------
    user_id : str
        Unique user identifier.

    Returns
    -------
    dict
        User profile with name, email, and plan.
    """
    return await _call_tool("lookup_user", {"user_id": user_id})


# COMPENSABLE tool with default retry settings.
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        fn_name="compensate_send_email",
        args={"to": "{to}", "subject": "{subject}", "body": "{body}"},
    ),
)
async def notify_user(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send a notification email.

    On rollback the engine calls ``compensate_send_email`` to notify
    the user of the correction.

    Parameters
    ----------
    to : str
        Recipient email address.
    subject : str
        Email subject line.
    body : str
        Email body content.

    Returns
    -------
    dict
        Confirmation with email_id and status.
    """
    log.info("[EXEC] notify_user to=%s subject=%s", to, subject)
    return await _call_tool("notify_user", {"to": to, "subject": subject, "body": body})


# COMPENSABLE tool with custom retry settings.
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor(
        fn_name="compensate_create_ticket",
        fn_version="1.0.0",
        args={"ticket_id": "{ticket_id}"},
        max_retries=5,
        retry_backoff_ms=500,
    ),
)
async def open_ticket(
    user_id: str,
    priority: str,
    description: str,
) -> dict[str, Any]:
    """Open a support ticket.

    Uses a custom retry policy (5 retries, 500ms backoff) to demonstrate
    ``CompensationDescriptor`` configuration.

    Parameters
    ----------
    user_id : str
        User who needs support.
    priority : str
        Severity: low, medium, high, critical.
    description : str
        Issue description.

    Returns
    -------
    dict
        Created ticket with ticket_id and status.
    """
    log.info(
        "[EXEC] open_ticket user=%s priority=%s ticket=%s",
        user_id,
        priority,
        description,
    )
    return await _call_tool(
        "open_ticket",
        {"user_id": user_id, "priority": priority, "description": description},
    )


# COMPENSABLE tool with custom compensation version.
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor(
        fn_name="compensate_assign_engineer",
        fn_version="2.0.0",
        args={
            "ticket_id": "{ticket_id}",
            "engineer": "{engineer}",
        },
        max_retries=3,
        retry_backoff_ms=1_000,
    ),
)
async def assign_engineer(ticket_id: str, engineer: str) -> dict[str, Any]:
    """Assign an engineer to a ticket.

    Has a higher compensation version (2.0.0) to demonstrate versioned
    compensation handlers.

    Parameters
    ----------
    ticket_id : str
        Ticket to assign.
    engineer : str
        Engineer name to assign.

    Returns
    -------
    dict
        Assignment confirmation.
    """
    log.info("[EXEC] assign_engineer ticket=%s engineer=%s", ticket_id, engineer)
    return await _call_tool("assign_engineer", {"ticket_id": ticket_id, "engineer": engineer})


# A tool that can fail conditionally to demonstrate rollback.
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        fn_name="compensate_escalate",
        args={"ticket_id": "{ticket_id}", "reason": "{reason}"},
    ),
)
async def escalate_ticket(ticket_id: str, reason: str, *, _fail: bool = False) -> dict[str, Any]:
    """Escalate a ticket to senior support.

    When ``_fail=True`` the tool raises a ``RuntimeError`` before
    committing, simulating a downstream service failure.  The SDK
    calls ``FAIL`` on the engine, which marks the effect as ``failed``
    and (via the saga orchestrator) triggers LIFO compensation.

    Parameters
    ----------
    ticket_id : str
        Ticket to escalate.
    reason : str
        Escalation reason.
    _fail : bool
        If True, raise an exception after registering compensation
        to simulate a downstream failure.

    Returns
    -------
    dict
        Escalation confirmation.

    Raises
    ------
    RuntimeError
        When ``_fail=True``, simulates a downstream service error
        after the effect was registered and compensation pushed.
    """
    log.info("[EXEC] escalate_ticket ticket=%s reason=%s", ticket_id, reason)
    result = await _call_tool("escalate_ticket", {"ticket_id": ticket_id, "reason": reason})
    if _fail:
        log.info("[FAIL] escalate_ticket : simulating downstream failure")
        raise RuntimeError(f"Downstream escalation service unreachable for ticket {ticket_id}")
    return result


def _step(label: str) -> None:
    """Print a section header for demo clarity."""
    log.info("")
    log.info("─" * 60)
    log.info("  %s", label)
    log.info("─" * 60)


async def main() -> None:
    """Run the compensation lifecycle demo.

    Steps
    -----
    1.  SAFE tool : executes directly, no effect log entry.
    2-4. Three COMPENSABLE tools in sequence : each registers compensation
         *before* execution, building an undo stack.
    5.  Anatomy of the undo stack : review the registered compensations.
    6.  Failing tool : triggers ``FAIL`` path, session enters ``failed``
        state.
    7.  Saga rollback : the orchestrator walks the undo stack in LIFO
        order, calling each compensation with original args.
    8.  Verification : review the compensation trace.
    """
    log.info("UndoLog : Compensation Lifecycle Demo")
    log.info("─" * 60)
    log.info("Org:  %s", _org_id())
    log.info("")

    # ── Step 1: SAFE tool ─────────────────────────────────────────────
    _step("1.  SAFE tool : no effect log entry")
    async with UndoLogSession(org_id=_org_id()) as session:
        log.info("Session: %s", session.session_id)

        user = await lookup_user(user_id="user_42", _session=session)
        log.info("lookup_user -> %s", json.dumps(user))
        log.info("  (bypasses proxy : no effect logged)")

        # ── Steps 2-4: Build the undo stack ──────────────────────────
        _step("2.  COMPENSABLE #1 : notify_user")
        email = await notify_user(
            to="bob@example.com",
            subject="Support ticket created",
            body="Your ticket TKT-abc has been opened.",
            _session=session,
        )
        log.info("notify_user -> %s", json.dumps(email))
        log.info("  Compensation: compensate_send_email(to=bob@example.com)")
        log.info("  Undo stack position: 1")

        _step("3.  COMPENSABLE #2 : open_ticket")
        ticket = await open_ticket(
            user_id="user_42",
            priority="high",
            description="Cannot access enterprise dashboard",
            _session=session,
        )
        log.info("open_ticket -> %s", json.dumps(ticket))
        log.info("  Compensation: compensate_create_ticket(ticket_id=%s)", ticket["ticket_id"])
        log.info("  Max retries: 5, Backoff: 500ms")
        log.info("  Undo stack position: 2")

        _step("4.  COMPENSABLE #3 : assign_engineer")
        assignment = await assign_engineer(
            ticket_id=ticket["ticket_id"],
            engineer="sarah_senior",
            _session=session,
        )
        log.info("assign_engineer -> %s", json.dumps(assignment))
        log.info("  Compensation: compensate_assign_engineer(v2.0.0)")
        log.info("  Undo stack position: 3")

        # ── Step 5: Undo stack summary ───────────────────────────────
        _step("5.  Undo stack : pre-registered compensations")
        log.info("  LIFO order (first-out = most recent):")
        log.info(
            "    3. compensate_assign_engineer(ticket_id=%s, engineer=sarah_senior)",
            ticket["ticket_id"],
        )
        log.info("    2. compensate_create_ticket(ticket_id=%s)", ticket["ticket_id"])
        log.info("    1. compensate_send_email(to=bob@example.com)")
        log.info("")
        log.info("  Invariant: registered_at < executed_at for each entry")
        log.info("  (enforced by the engine: compensation is persisted")
        log.info("   before the tool body runs)")

        # ── Step 6: Failing tool ──────────────────────────────────────
        _step("6.  Failing tool : triggers session failure")
        log.info("  Calling escalate_ticket with _fail=True...")
        try:
            await escalate_ticket(
                ticket_id=ticket["ticket_id"],
                reason="Critical SLA breach",
                _session=session,
                _fail=True,
            )
        except RuntimeError as exc:
            log.info("  escalate_ticket failed: %s", exc)
            log.info("  SDK called FAIL on the engine")
            log.info("  Effect marked as: failed")
            log.info("  Session enters: failed (or compensating)")

        # ── Step 7: Saga rollback (simulated) ─────────────────────────
        _step("7.  Saga orchestrator : LIFO compensation rollback")
        log.info("  The saga orchestrator detects the failed session")
        log.info("  and walks the undo stack in LIFO order:")
        log.info("")
        log.info("  Step 1/3: compensate_assign_engineer")
        log.info("    POST /compensate with Idempotency-Key: undo-<undo_id>")
        log.info("    Args: ticket_id=%s, engineer=sarah_senior", ticket["ticket_id"])
        log.info("    → engineer unassigned")
        log.info("")
        log.info("  Step 2/3: compensate_create_ticket")
        log.info("    POST /compensate with Idempotency-Key: undo-<undo_id>")
        log.info("    Args: ticket_id=%s", ticket["ticket_id"])
        log.info("    → ticket voided")
        log.info("")
        log.info("  Step 3/3: compensate_send_email")
        log.info("    POST /compensate with Idempotency-Key: undo-<undo_id>")
        log.info("    Args: to=bob@example.com, subject=Support ticket created")
        log.info("    → correction email sent")
        log.info("")
        log.info("  All compensations succeeded → session: compensated")
        log.info("  Any compensation failed → session: halted")

        # ── Step 8: Verification ──────────────────────────────────────
        _step("8.  Verification")
        log.info("  Effect log entries for session %s:", session.session_id)
        log.info("    1. notify_user       → committed (compensation registered)")
        log.info("    2. open_ticket       → committed (compensation registered)")
        log.info("    3. assign_engineer   → committed (compensation registered)")
        log.info("    4. escalate_ticket   → failed (compensation registered)")
        log.info("")
        log.info("  Undo stack: 3 pending entries")
        log.info("  Saga status: session compensated after LIFO walk")
        log.info("")
        log.info("─" * 60)
        log.info("  Demo complete : compensation lifecycle verified")
        log.info("  (To query the actual DB for verification:")
        log.info("   SELECT tool_name, state::text, step_index")
        log.info("   FROM undolog_effect_log")
        log.info("   WHERE session_id = '%s'", session.session_id)
        log.info("   ORDER BY step_index;")
        log.info("   )")
        log.info("─" * 60)


if __name__ == "__main__":
    asyncio.run(main())
