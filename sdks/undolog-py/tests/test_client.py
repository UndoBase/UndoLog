"""Tests for UndoLogClient HTTP methods.

Covers intercept (all outcomes), commit/fail no-ops through proxy,
approve/reject lifecycle, and unknown-status failure mode. All tests
use ``httpx.MockTransport`` against real proxy response shapes.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from undolog_sdk.client import UndoLogClient


def _client(handler: Any) -> UndoLogClient:
    """Create an ``UndoLogClient`` backed by a ``MockTransport`` handler."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8080"
    )
    return UndoLogClient(http_client=http_client)


# ── intercept ──────────────────────────────────────────────────────────────


class TestIntercept:
    """Verify ``intercept`` maps every proxy status to the correct outcome."""

    async def test_execute_outcome(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "executed",
                    "effect_id": "eff-1",
                    "result": {"ok": True},
                },
            )

        client = _client(_handler)
        resp = await client.intercept(
            org_id="org",
            session_id="11111111-1111-1111-1111-111111111111",
            tool_name="transfer_funds",
            step_index=1,
            args={"amount": 100},
        )
        assert resp.outcome == "Execute"
        assert resp.effect_id == "eff-1"
        assert resp.approval_id is None

    async def test_replay_outcome(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "replayed",
                    "effect_id": "eff-2",
                    "result": {"success": True, "output": "cached-result"},
                },
            )

        client = _client(_handler)
        resp = await client.intercept(
            org_id="org",
            session_id="11111111-1111-1111-1111-111111111111",
            tool_name="transfer_funds",
            step_index=1,
            args={"amount": 100},
        )
        assert resp.outcome == "Replay"
        assert resp.effect_id == "eff-2"
        assert resp.cached_result == {"success": True, "output": "cached-result"}

    async def test_awaiting_approval_outcome(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "pending_approval",
                    "approval_id": "apr-1",
                    "retry_after": 5,
                },
            )

        client = _client(_handler)
        resp = await client.intercept(
            org_id="org",
            session_id="11111111-1111-1111-1111-111111111111",
            tool_name="delete_database",
            step_index=1,
            args={"target": "prod"},
        )
        assert resp.outcome == "AwaitingApproval"
        assert resp.approval_id == "apr-1"
        assert resp.effect_id is None

    async def test_unknown_status_raises(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "unknown_thing"})

        client = _client(_handler)
        with pytest.raises(ValueError, match="Unexpected proxy status"):
            await client.intercept(
                org_id="org",
                session_id="11111111-1111-1111-1111-111111111111",
                tool_name="t",
                step_index=1,
                args={},
            )

    async def test_sends_correct_headers(self) -> None:
        captured_headers: dict[str, str] = {}

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_headers
            captured_headers = dict(request.headers)
            return httpx.Response(
                200,
                json={"status": "executed", "effect_id": "eff-1"},
            )

        client = UndoLogClient(
            api_key="test-key",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(_handler),
                base_url="http://localhost:8080",
            ),
        )
        await client.intercept(
            org_id="org-abc",
            session_id="11111111-1111-1111-1111-111111111111",
            tool_name="t",
            step_index=1,
            args={},
        )
        # httpx lowercases header names on the wire.
        lower = {k.lower(): v for k, v in captured_headers.items()}
        assert lower["x-undolog-org-id"] == "org-abc"
        assert lower["x-undolog-session-id"] == "11111111-1111-1111-1111-111111111111"
        assert lower["x-api-key"] == "test-key"

    async def test_http_error_propagates(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

        client = _client(_handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.intercept(
                org_id="org",
                session_id="11111111-1111-1111-1111-111111111111",
                tool_name="t",
                step_index=1,
                args={},
            )


# ── commit / fail (no-op through proxy) ────────────────────────────────────


class TestCommitNoop:
    """``commit()`` returns ``{}`` when the proxy 404s (inline commit)."""

    async def test_returns_empty_on_404(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = _client(_handler)
        result = await client.commit(
            "org", "11111111-1111-1111-1111-111111111111", "eff-1", {"ok": True}
        )
        assert result == {}

    async def test_returns_body_on_success(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "committed"})

        client = _client(_handler)
        result = await client.commit(
            "org", "11111111-1111-1111-1111-111111111111", "eff-1", {"ok": True}
        )
        assert result == {"status": "committed"}


class TestFailNoop:
    """``fail()`` returns ``{}`` when the proxy 404s (inline failure)."""

    async def test_returns_empty_on_404(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = _client(_handler)
        result = await client.fail(
            "org", "11111111-1111-1111-1111-111111111111", "eff-1", "timeout"
        )
        assert result == {}


# ── approve / reject ───────────────────────────────────────────────────────


class TestApprove:
    """``approve()`` sends POST to ``/approvals/{id}/approve``."""

    async def test_success(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "approved",
                    "approval_id": "apr-1",
                    "effect_id": "eff-1",
                    "execution": "committed",
                    "result": {"ok": True},
                },
            )

        client = _client(_handler)
        result = await client.approve("org", "apr-1")
        assert result["status"] == "approved"
        assert result["execution"] == "committed"
        assert result["effect_id"] == "eff-1"

    async def test_not_found(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})

        client = _client(_handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.approve("org", "bad-id")

    async def test_conflict(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": "already resolved"})

        client = _client(_handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.approve("org", "apr-1")


class TestReject:
    """``reject()`` sends POST to ``/approvals/{id}/reject``."""

    async def test_success(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status": "rejected", "approval_id": "apr-1"},
            )

        client = _client(_handler)
        result = await client.reject("org", "apr-1")
        assert result["status"] == "rejected"
        assert result["approval_id"] == "apr-1"

    async def test_not_found(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})

        client = _client(_handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.reject("org", "bad-id")

    async def test_conflict(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": "already resolved"})

        client = _client(_handler)
        with pytest.raises(httpx.HTTPStatusError):
            await client.reject("org", "apr-1")


# ── async context manager ──────────────────────────────────────────────────


class TestAsyncContextManager:
    """``UndoLogClient`` works as an async context manager."""

    async def test_enter_returns_self(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        client = _client(_handler)
        async with client as ctx:
            assert ctx is client

    async def test_exit_closes_http_client(self) -> None:
        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        client = _client(_handler)
        async with client:
            pass
        assert client._http.is_closed
