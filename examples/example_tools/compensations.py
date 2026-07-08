"""
Compensation handlers called by the UndoLog saga orchestrator on rollback.

When a multi-step workflow fails after some COMPENSABLE tools have already
committed, the engine executes these handlers in LIFO order to undo each
completed step.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def compensate_send_email(args: dict) -> dict:
    """Send a correction email to undo the original send.

    Parameters
    ----------
    args : dict
        Must contain ``to`` (str) and optionally ``subject`` (str).

    Returns
    -------
    dict
        Confirmation with ``status``, ``to``, and ``original_subject``.
    """
    customer_email = args.get("to", "unknown")
    original_subject = args.get("subject", "")
    log.info("[COMPENSATE] Sending correction to %s", customer_email)
    return {
        "status": "correction_sent",
        "to": customer_email,
        "original_subject": original_subject,
    }


def compensate_create_ticket(args: dict) -> dict:
    """Close the ticket created during the failed workflow.

    Parameters
    ----------
    args : dict
        Must contain ``ticket_id`` (str).

    Returns
    -------
    dict
        Confirmation with ``status`` and ``ticket_id``.
    """
    ticket_id = args.get("ticket_id", "unknown")
    log.info("[COMPENSATE] Closing ticket #%s", ticket_id)
    return {"status": "ticket_closed", "ticket_id": ticket_id}


def compensate_assign_engineer(args: dict) -> dict:
    """Unassign an engineer from a ticket.

    Parameters
    ----------
    args : dict
        Must contain ``ticket_id`` (str) and ``engineer`` (str).

    Returns
    -------
    dict
        Confirmation with ``status``, ``ticket_id``, and ``engineer``.
    """
    ticket_id = args.get("ticket_id", "unknown")
    engineer = args.get("engineer", "unknown")
    log.info("[COMPENSATE] Unassigning %s from ticket #%s", engineer, ticket_id)
    return {
        "status": "engineer_unassigned",
        "ticket_id": ticket_id,
        "engineer": engineer,
    }


def compensate_escalate(args: dict) -> dict:
    """Reverse an escalation.

    Parameters
    ----------
    args : dict
        Must contain ``ticket_id`` (str) and ``reason`` (str).

    Returns
    -------
    dict
        Confirmation with ``status``, ``ticket_id``, and ``reason``.
    """
    ticket_id = args.get("ticket_id", "unknown")
    reason = args.get("reason", "unknown")
    log.info("[COMPENSATE] Reversing escalation for ticket #%s: %s", ticket_id, reason)
    return {
        "status": "escalation_reversed",
        "ticket_id": ticket_id,
        "reason": reason,
    }


def compensate_charge_payment(args: dict) -> dict:
    """Reverse a payment charge.

    Parameters
    ----------
    args : dict
        Must contain ``amount`` (int/float) and ``currency`` (str).

    Returns
    -------
    dict
        Confirmation with ``status``, ``amount``, and ``currency``.
    """
    amount = args.get("amount", 0)
    currency = args.get("currency", "unknown")
    log.info("[COMPENSATE] Reversing charge: %s %s", amount, currency)
    return {
        "status": "charge_reversed",
        "amount": amount,
        "currency": currency,
    }
