"""
Customer support tools wrapped with UndoLog effect tracking.

Each tool is decorated with a tier that defines how UndoLog handles
its execution, replay, and rollback behaviour.

Tiers
-----
SAFE
    No side effects: replayed freely on cache miss.
COMPENSABLE
    Side effects are reversible via a compensation handler.
IRREVERSIBLE
    Side effects cannot be undone: requires human approval.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from undolog_sdk import CompensationDescriptor, ToolTier, undolog_tool

log = logging.getLogger(__name__)


@undolog_tool(tier=ToolTier.SAFE)
async def lookup_customer(customer_id: str) -> dict:
    """Look up customer information by ID.

    Parameters
    ----------
    customer_id : str
        Unique identifier for the customer.

    Returns
    -------
    dict
        Customer profile with name, email, plan, and support level.
    """
    return {
        "customer_id": customer_id,
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "plan": "enterprise",
        "support_level": "premium",
    }


@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        fn_name="compensate_send_email",
        args={"to": "{to}", "subject": "{subject}"},
    ),
)
async def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email to a customer.

    On workflow rollback the engine calls ``compensate_send_email``
    to notify the customer of the correction.

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
        Confirmation with ``email_id`` and delivery ``status``.
    """
    log.info("[EMAIL] To: %s, Subject: %s", to, subject)
    return {"email_id": "email_" + str(hash(to + subject))[:12], "status": "sent"}


@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        fn_name="compensate_create_ticket",
        args={"ticket_id": "{ticket_id}"},
    ),
)
async def create_ticket(customer_id: str, priority: str, description: str) -> dict:
    """Create a support ticket in the system.

    On workflow rollback the engine calls ``compensate_create_ticket``
    to void the ticket.

    Parameters
    ----------
    customer_id : str
        Customer who reported the issue.
    priority : str
        Severity level (low, medium, high, critical).
    description : str
        Detailed issue description.

    Returns
    -------
    dict
        Created ticket with ``ticket_id``, ``status``, and ``priority``.
    """
    ticket_id = "TKT-" + str(hash(customer_id + description))[-8:]
    log.info("[TICKET] Created #%s (priority=%s, customer=%s)", ticket_id, priority, customer_id)
    return {"ticket_id": ticket_id, "status": "open", "priority": priority}


@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def escalate_case(ticket_id: str, reason: str) -> dict:
    """Escalate a case to the priority support queue.

    Because escalation is irreversible the engine pauses execution
    and creates a pending approval request.  A human must approve
    before the tool body runs.

    Parameters
    ----------
    ticket_id : str
        Ticket to escalate.
    reason : str
        Justification for escalation.

    Returns
    -------
    dict
        Escalation confirmation with ``ticket_id`` and ``status``.
    """
    log.info("[ESCALATE] Ticket #%s escalated: %s", ticket_id, reason)
    return {"ticket_id": ticket_id, "status": "escalated", "reason": reason}


def get_tool_registry() -> dict[str, Callable[..., Awaitable[dict]]]:
    """Return a name-to-function mapping for the four support tools.

    Returns
    -------
    dict
        Keys are tool names, values are the decorated async functions.
    """
    return {
        "lookup_customer": lookup_customer,
        "send_email": send_email,
        "create_ticket": create_ticket,
        "escalate_case": escalate_case,
    }
