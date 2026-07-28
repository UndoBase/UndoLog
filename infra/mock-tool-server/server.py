"""Stateful mock upstream MCP tool server for UndoLog demos.

Provides realistic CRUD operations for all demo tools, with an
in-memory store seeded with sample data at startup.

The server supports Idempotency-Key based deduplication for
compensation requests: when the Rust saga orchestrator sends
``Idempotency-Key: undo-{undo_id}`` on a retry, a previously cached
response is returned without re-executing the handler.

Endpoints
---------
POST /tools
    Execute a tool call.  Request body is a ``ToolCall`` JSON object.
    Response is a ``ToolResult`` JSON object.  Supports
    ``Idempotency-Key`` header for dedup.

GET /health
    Returns ``{"status": "ok"}``.

Environment
-----------
MOCK_TOOL_SERVER_ADDR : str
    Listen address (default ``0.0.0.0``).
MOCK_TOOL_SERVER_PORT : int
    Listen port (default ``9091``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mock-tool-server")


# ── Seed data ──────────────────────────────────────────────────────────────

SEED_CUSTOMERS: dict[str, dict[str, str]] = {
    "cust_42": {
        "customer_id": "cust_42",
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "plan": "enterprise",
        "support_level": "premium",
    },
    "cust_1": {
        "customer_id": "cust_1",
        "name": "Bob Smith",
        "email": "bob@example.com",
        "plan": "enterprise",
        "support_level": "standard",
    },
    "user_42": {
        "user_id": "user_42",
        "name": "Bob Smith",
        "email": "bob@example.com",
        "plan": "enterprise",
    },
    "user_1": {
        "user_id": "user_1",
        "name": "Carol Davis",
        "email": "carol@example.com",
        "plan": "premium",
    },
}

SEED_PLANS: dict[str, dict[str, Any]] = {
    "enterprise": {
        "plan_id": "enterprise",
        "name": "Enterprise",
        "monthly_price": 999,
        "features": ["unlimited_tickets", "priority_support", "api_access"],
    },
    "premium": {
        "plan_id": "premium",
        "name": "Premium",
        "monthly_price": 299,
        "features": ["unlimited_tickets", "priority_support"],
    },
    "basic": {
        "plan_id": "basic",
        "name": "Basic",
        "monthly_price": 49,
        "features": ["10_tickets_per_month"],
    },
}

SEED_ENGINEERS: dict[str, dict[str, str]] = {
    "sarah": {"engineer": "sarah", "name": "Sarah Chen", "team": "senior"},
    "mike": {"engineer": "mike", "name": "Mike Rivera", "team": "level-2"},
}


# ── Idempotency store (compensation dedup) ────────────────────────────────

_IDEM_STORE: dict[str, dict[str, Any]] = {}


# ── In-memory store (mutated at runtime) ──────────────────────────────────

_store: dict[str, dict[str, Any]] = {
    "customers": dict(SEED_CUSTOMERS),
    "plans": dict(SEED_PLANS),
    "engineers": dict(SEED_ENGINEERS),
    "tickets": {},
    "emails": {},
    "payments": {},
}


# ── Internal handlers ────────────────────────────────────────────────────


def _ok(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "output": json.dumps(output),
        "duration_ms": 0,
    }


def _handle_lookup_customer(args: dict[str, Any]) -> dict[str, Any]:
    customer_id = args.get("customer_id", "")
    customer = _store["customers"].get(customer_id)
    if not customer:
        return {"success": False, "error": f"Customer {customer_id!r} not found"}
    return _ok(customer)


def _handle_lookup_user(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id", "")
    user = _store["customers"].get(user_id)
    if not user:
        return {"success": False, "error": f"User {user_id!r} not found"}
    return _ok(user)


def _handle_lookup_plan(args: dict[str, Any]) -> dict[str, Any]:
    plan_id = args.get("plan_id", "")
    plan = _store["plans"].get(plan_id)
    if not plan:
        return {"success": False, "error": f"Plan {plan_id!r} not found"}
    return _ok(plan)


def _handle_send_email(args: dict[str, Any]) -> dict[str, Any]:
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    key = f"{to}:{subject}:{body}"
    email_id = hashlib.sha256(key.encode()).hexdigest()[:16]
    if email_id not in _store["emails"]:
        _store["emails"][email_id] = {
            "email_id": email_id,
            "to": to,
            "subject": subject,
            "body": body,
            "status": "sent",
        }
    return _ok({"email_id": email_id, "status": "sent"})


def _handle_create_ticket(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    _store["tickets"][ticket_id] = {
        "ticket_id": ticket_id,
        "customer_id": args.get("customer_id", args.get("user_id", "")),
        "priority": args.get("priority", "low"),
        "description": args.get("description", ""),
        "status": "open",
    }
    return _ok(
        {
            "ticket_id": ticket_id,
            "status": "open",
            "priority": args.get("priority", "low"),
        }
    )


def _handle_assign_engineer(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticket_id", "")
    engineer = args.get("engineer", "")
    ticket = _store["tickets"].get(ticket_id)
    if not ticket:
        return {"success": False, "error": f"Ticket {ticket_id!r} not found"}
    ticket["engineer"] = engineer
    ticket["status"] = "assigned"
    return _ok({"ticket_id": ticket_id, "engineer": engineer, "status": "assigned"})


def _handle_escalate(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticket_id", "")
    reason = args.get("reason", "")
    ticket = _store["tickets"].get(ticket_id)
    if not ticket:
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": "unknown",
            "priority": "low",
            "description": f"Auto-created for escalation: {reason}",
            "status": "open",
        }
        _store["tickets"][ticket_id] = ticket
    ticket["status"] = "escalated"
    ticket["escalation_reason"] = reason
    return _ok({"ticket_id": ticket_id, "status": "escalated", "reason": reason})


def _handle_charge_payment(args: dict[str, Any]) -> dict[str, Any]:
    payment_id = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    amount = args.get("amount", 0)
    currency = args.get("currency", "USD")
    _store["payments"][payment_id] = {
        "payment_id": payment_id,
        "amount": amount,
        "currency": currency,
        "status": "processed",
    }
    return _ok(
        {
            "payment_id": payment_id,
            "amount": amount,
            "currency": currency,
            "status": "processed",
        }
    )


# ── Compensation handlers (reversal operations) ───────────────────────────


def _handle_compensate_send_email(args: dict[str, Any]) -> dict[str, Any]:
    to = args.get("to", "unknown")
    subject = args.get("subject", "")
    corr_id = f"CORR-{uuid.uuid4().hex[:8].upper()}"
    _store["emails"][corr_id] = {
        "email_id": corr_id,
        "to": to,
        "original_subject": subject,
        "type": "correction",
        "status": "sent",
    }
    return _ok({"status": "correction_sent", "to": to, "original_subject": subject})


def _handle_compensate_create_ticket(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticket_id", "unknown")
    ticket = _store["tickets"].get(ticket_id)
    if ticket:
        ticket["status"] = "closed"
    return _ok({"status": "ticket_closed", "ticket_id": ticket_id})


def _handle_compensate_assign_engineer(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticket_id", "unknown")
    engineer = args.get("engineer", "unknown")
    ticket = _store["tickets"].get(ticket_id)
    if ticket and ticket.get("engineer") == engineer:
        ticket["status"] = "open"
        ticket.pop("engineer", None)
    return _ok(
        {"status": "engineer_unassigned", "ticket_id": ticket_id, "engineer": engineer}
    )


def _handle_compensate_escalate(args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = args.get("ticket_id", "unknown")
    reason = args.get("reason", "unknown")
    ticket = _store["tickets"].get(ticket_id)
    if ticket:
        ticket["status"] = "open"
        ticket.pop("escalation_reason", None)
    return _ok(
        {"status": "escalation_reversed", "ticket_id": ticket_id, "reason": reason}
    )


def _handle_compensate_charge_payment(args: dict[str, Any]) -> dict[str, Any]:
    amount = args.get("amount", 0)
    currency = args.get("currency", "unknown")
    reversal_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
    _store["payments"][reversal_id] = {
        "payment_id": reversal_id,
        "amount": amount,
        "currency": currency,
        "status": "reversed",
    }
    return _ok({"status": "charge_reversed", "amount": amount, "currency": currency})


def _handle_integration_test(args: dict[str, Any]) -> dict[str, Any]:
    return _ok({"received": args, "ok": True})


# ── Dispatch table ────────────────────────────────────────────────────────

HANDLERS: dict[str, Any] = {
    "lookup_customer": _handle_lookup_customer,
    "lookup_user": _handle_lookup_user,
    "lookup_plan": _handle_lookup_plan,
    "send_email": _handle_send_email,
    "notify_user": _handle_send_email,
    "create_ticket": _handle_create_ticket,
    "open_ticket": _handle_create_ticket,
    "assign_engineer": _handle_assign_engineer,
    "escalate_case": _handle_escalate,
    "escalate_ticket": _handle_escalate,
    "charge_payment": _handle_charge_payment,
    "integration_test_tool": _handle_integration_test,
    "compensate_send_email": _handle_compensate_send_email,
    "compensate_create_ticket": _handle_compensate_create_ticket,
    "compensate_assign_engineer": _handle_compensate_assign_engineer,
    "compensate_escalate": _handle_compensate_escalate,
    "compensate_charge_payment": _handle_compensate_charge_payment,
}


# ── HTTP handler ──────────────────────────────────────────────────────────


TOOL_CALLS: list[dict[str, Any]] = []


class Handler(BaseHTTPRequestHandler):
    """HTTP handler that dispatches tool calls to internal handlers."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response({"status": "ok"})
            return
        self._json_response({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/tools":
            self._json_response({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        try:
            call = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(
                {"success": False, "error": "invalid JSON"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        tool_name = call.get("tool_name", "unknown")
        args = call.get("args", {})
        log.info("TOOL CALL: %s args=%s", tool_name, json.dumps(args))

        idem_key = self.headers.get("Idempotency-Key", "")
        if idem_key:
            cached = _IDEM_STORE.get(idem_key)
            if cached is not None:
                log.info("IDEMPOTENCY HIT: key=%s tool=%s", idem_key, tool_name)
                self._json_response(cached)
                return

        TOOL_CALLS.append(
            {"tool_name": tool_name, "args": args, "timestamp": time.time()}
        )

        handler = HANDLERS.get(tool_name)
        if not handler:
            self._json_response(
                {"success": False, "error": f"Unknown tool: {tool_name!r}"},
                HTTPStatus.NOT_FOUND,
            )
            return

        result = handler(args)
        if idem_key:
            _IDEM_STORE[idem_key] = result
        http_status = (
            HTTPStatus.OK if result.get("success", False) else HTTPStatus.NOT_FOUND
        )
        self._json_response(result, http_status)

    def _json_response(self, data: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug(fmt, *args)


def run() -> None:
    """Start the HTTP server and block forever."""
    addr = os.environ.get("MOCK_TOOL_SERVER_ADDR", "0.0.0.0")
    port = int(os.environ.get("MOCK_TOOL_SERVER_PORT", "9091"))
    server = HTTPServer((addr, port), Handler)
    log.info("Listening on %s:%s", addr, port)
    log.info("Endpoints: POST /tools, GET /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    run()
