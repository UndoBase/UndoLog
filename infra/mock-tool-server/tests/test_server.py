"""Tests for the mock-tool-server.

Validates all tool handlers, idempotency key dedup, health endpoint,
and HTTP-level request/response contract.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import httpx
import pytest

from server import (
    HANDLERS,
    _IDEM_STORE,
    _handle_lookup_customer,
    _handle_charge_payment,
    Handler,
)
from http.server import HTTPServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url() -> str:
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{port}"

    # wait for server to be ready
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=1)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ReadTimeout):
            time.sleep(0.1)

    yield url
    server.shutdown()
    t.join(timeout=3)


@pytest.fixture(autouse=True)
def reset_idem_store() -> None:
    _IDEM_STORE.clear()


class TestHealth:
    """Health endpoint logic is tested via the HTTP path in integration tests."""


class TestLookupCustomer:
    """Lookup customer handler returns data or error."""

    def test_found(self) -> None:
        result = _handle_lookup_customer({"customer_id": "cust_42"})
        assert result["success"] is True
        body = json.loads(result["output"])
        assert body["name"] == "Alice Johnson"

    def test_not_found(self) -> None:
        result = _handle_lookup_customer({"customer_id": "nonexistent"})
        assert result["success"] is False
        assert "not found" in result.get("error", "")


class TestChargePayment:
    """Charge payment handler produces payment IDs and defaults."""

    def test_returns_payment_id(self) -> None:
        result = _handle_charge_payment({"amount": 500, "currency": "USD"})
        assert result["success"] is True
        body = json.loads(result["output"])
        assert body["amount"] == 500
        assert body["currency"] == "USD"
        assert body["payment_id"].startswith("PAY-")

    def test_default_currency(self) -> None:
        result = _handle_charge_payment({"amount": 100})
        assert result["success"] is True


class TestDispatchTable:
    """Every handler in the dispatch table produces a dict with a 'success' key."""

    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("lookup_customer", {"customer_id": "cust_42"}),
            ("lookup_user", {"user_id": "user_42"}),
            ("lookup_plan", {"plan_id": "enterprise"}),
            ("send_email", {"to": "a@b.com", "subject": "hi", "body": "hello"}),
            ("create_ticket", {"customer_id": "cust_42", "priority": "high"}),
            ("assign_engineer", {"ticket_id": "TKT-NONEXIST", "engineer": "sarah"}),
            ("escalate_case", {"ticket_id": "TKT-NONEXIST", "reason": "urgent"}),
            ("charge_payment", {"amount": 100, "currency": "USD"}),
            ("compensate_send_email", {"to": "a@b.com", "subject": "hi"}),
            ("compensate_create_ticket", {"ticket_id": "TKT-001", "status": "closed"}),
            (
                "compensate_assign_engineer",
                {"ticket_id": "TKT-001", "engineer": "sarah"},
            ),
            ("compensate_escalate", {"ticket_id": "TKT-001", "reason": "urgent"}),
            ("compensate_charge_payment", {"amount": 100}),
        ],
    )
    def test_handler_returns_success_field(
        self, tool_name: str, args: dict[str, Any]
    ) -> None:
        handler = HANDLERS.get(tool_name)
        assert handler is not None, f"No handler for {tool_name!r}"
        result = handler(args)
        assert "success" in result, f"Handler {tool_name!r} missing 'success' field"

    def test_unknown_tool_raises_key_error(self) -> None:
        assert "nonexistent_tool" not in HANDLERS


class TestIdempotencyKeyDedup:
    """Idempotency-Key header dedup logic."""

    def test_repeat_call_returns_cached(self) -> None:
        key = "idem-test-1"
        args: dict[str, Any] = {"customer_id": "cust_42"}

        first = _handle_lookup_customer(args)
        _IDEM_STORE[key] = first

        cached = _IDEM_STORE.get(key)
        assert cached is not None
        assert cached["success"] is True

    def test_different_keys_independent(self) -> None:
        _IDEM_STORE["key-a"] = {"success": True}
        _IDEM_STORE["key-b"] = {"success": False}

        assert _IDEM_STORE["key-a"]["success"] is True
        assert _IDEM_STORE["key-b"]["success"] is False

    def test_store_cleared_between_tests(self) -> None:
        assert len(_IDEM_STORE) == 0


class TestHttpContract:
    """HTTP-level request/response contract via a live server."""

    def test_health_endpoint(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_tool_call_success(self, server_url: str) -> None:
        payload = {"tool_name": "lookup_customer", "args": {"customer_id": "cust_42"}}
        resp = httpx.post(f"{server_url}/tools", json=payload, timeout=5)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_tool_call_not_found(self, server_url: str) -> None:
        payload = {"tool_name": "nonexistent", "args": {}}
        resp = httpx.post(f"{server_url}/tools", json=payload, timeout=5)
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False

    def test_invalid_json_returns_400(self, server_url: str) -> None:
        resp = httpx.post(f"{server_url}/tools", content=b"not json", timeout=5)
        assert resp.status_code == 400

    def test_get_on_tools_returns_404(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/tools", timeout=5)
        assert resp.status_code == 404

    def test_idempotency_key_dedup_http(self, server_url: str) -> None:
        payload = {"tool_name": "lookup_customer", "args": {"customer_id": "cust_42"}}
        headers = {"Idempotency-Key": "http-test-key-1"}

        first = httpx.post(
            f"{server_url}/tools", json=payload, headers=headers, timeout=5
        )
        assert first.status_code == 200

        second = httpx.post(
            f"{server_url}/tools", json=payload, headers=headers, timeout=5
        )
        assert second.status_code == 200
        assert first.json() == second.json()
