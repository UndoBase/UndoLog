"""Tests for the SSE dashboard consumer module.

Tests cover SSE wire format parsing, event dispatch, heartbeat
handling, and malformed input resilience.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sse_dashboard import Event, SSEConnection, display_event


class _AsyncIter:
    """Helper to wrap a list into an async iterator."""

    def __init__(self, items: list[str]) -> None:
        self._items = items

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> str:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _AsyncCtxMgr:
    """Wraps a value so it can be used with ``async with``.

    The ``__aenter__`` returns the wrapped value and ``__aexit__`` is a no-op.
    """

    def __init__(self, value: MagicMock) -> None:
        self._value = value

    async def __aenter__(self) -> MagicMock:
        return self._value

    async def __aexit__(self, *args: object) -> None:
        pass


def _mock_sse_stream(lines: list[str]) -> MagicMock:
    """Create a mock async response that yields *lines* as ``aiter_lines``."""
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.aiter_lines = MagicMock(return_value=_AsyncIter(lines))
    return resp


@pytest.mark.asyncio
async def test_parse_single_event() -> None:
    """A single well-formed SSE event produces one callback invocation."""
    received: list[Event] = []

    event_data = json.dumps(
        {
            "type": "effect_intercepted",
            "timestamp": "2026-07-09T12:34:56.789Z",
            "org_id": "org-alpha",
            "session_id": "sess-001",
            "effect_id": "eff-001",
        }
    )

    lines = [
        "event: effect_intercepted",
        f"data: {event_data}",
        "",
    ]

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.stream = MagicMock(return_value=_AsyncCtxMgr(_mock_sse_stream(lines)))

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        conn = SSEConnection(org_id="test-org", api_key="test-key")
        try:
            await conn.connect(callback=lambda ev: received.append(ev))
        except httpx.RequestError:
            # Mock stream exhausts before connect loop ends, expected.
            pass

    assert len(received) == 1
    ev = received[0]
    assert ev.type == "effect_intercepted"
    assert ev.org_id == "org-alpha"
    assert ev.session_id == "sess-001"
    assert ev.effect_id == "eff-001"


@pytest.mark.asyncio
async def test_parse_multiple_events() -> None:
    """Multiple events in one stream are dispatched in order."""
    received: list[Event] = []

    intercepted = json.dumps(
        {
            "type": "effect_intercepted",
            "timestamp": "2026-07-09T12:34:56.000Z",
            "org_id": "org-1",
            "session_id": "s-1",
        }
    )
    committed = json.dumps(
        {
            "type": "effect_committed",
            "timestamp": "2026-07-09T12:34:57.000Z",
            "org_id": "org-1",
            "session_id": "s-1",
            "effect_id": "e-1",
        }
    )

    lines = [
        "event: effect_intercepted",
        f"data: {intercepted}",
        "",
        "event: effect_committed",
        f"data: {committed}",
        "",
    ]

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.stream = MagicMock(return_value=_AsyncCtxMgr(_mock_sse_stream(lines)))

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        conn = SSEConnection(org_id="test-org", api_key="test-key")
        try:
            await conn.connect(callback=lambda ev: received.append(ev))
        except httpx.RequestError:
            # Mock stream exhausts before connect loop ends, expected.
            pass

    assert len(received) == 2
    assert received[0].type == "effect_intercepted"
    assert received[1].type == "effect_committed"


@pytest.mark.asyncio
async def test_heartbeat_ignored() -> None:
    """Heartbeat lines (``: ping``) are silently skipped."""
    received: list[Event] = []

    event_data = json.dumps(
        {
            "type": "effect_committed",
            "timestamp": "2026-07-09T12:34:56.000Z",
            "org_id": "org-1",
        }
    )

    lines = [
        ": ping",
        "",
        "event: effect_committed",
        f"data: {event_data}",
        "",
        ": ping",
        "",
    ]

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.stream = MagicMock(return_value=_AsyncCtxMgr(_mock_sse_stream(lines)))

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        conn = SSEConnection(org_id="test-org", api_key="test-key")
        try:
            await conn.connect(callback=lambda ev: received.append(ev))
        except httpx.RequestError:
            # Mock stream exhausts before connect loop ends, expected.
            pass

    assert len(received) == 1
    assert received[0].type == "effect_committed"


@pytest.mark.asyncio
async def test_malformed_json_skipped() -> None:
    """Events with invalid JSON data are silently dropped."""
    received: list[Event] = []

    lines = [
        "event: effect_intercepted",
        "data: {not json}",
        "",
    ]

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.stream = MagicMock(return_value=_AsyncCtxMgr(_mock_sse_stream(lines)))

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        conn = SSEConnection(org_id="test-org", api_key="test-key")
        try:
            await conn.connect(callback=lambda ev: received.append(ev))
        except httpx.RequestError:
            # Mock stream exhausts before connect loop ends, expected.
            pass

    assert len(received) == 0


@pytest.mark.asyncio
async def test_approval_event_includes_approval_id() -> None:
    """Approval events carry the approval_id field."""
    received: list[Event] = []

    event_data = json.dumps(
        {
            "type": "approval_required",
            "timestamp": "2026-07-09T12:34:56.000Z",
            "org_id": "org-1",
            "session_id": "s-1",
            "effect_id": "e-1",
            "approval_id": "apr-001",
        }
    )

    lines = [
        "event: approval_required",
        f"data: {event_data}",
        "",
    ]

    mock_client = MagicMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.stream = MagicMock(return_value=_AsyncCtxMgr(_mock_sse_stream(lines)))

    with patch.object(httpx, "AsyncClient", return_value=mock_client):
        conn = SSEConnection(org_id="test-org", api_key="test-key")
        try:
            await conn.connect(callback=lambda ev: received.append(ev))
        except httpx.RequestError:
            # Mock stream exhausts before connect loop ends, expected.
            pass

    assert len(received) == 1
    ev = received[0]
    assert ev.type == "approval_required"
    assert ev.approval_id == "apr-001"


def test_display_event_format() -> None:
    """display_event runs without error on every event type."""
    for event_type in [
        "effect_intercepted",
        "effect_executed",
        "effect_committed",
        "effect_replayed",
        "effect_failed",
        "approval_required",
        "approval_approved",
        "approval_rejected",
    ]:
        ev = Event(
            type=event_type,
            timestamp="2026-07-09T12:00:00.000Z",
            org_id="org-test",
            session_id="sess-001",
            effect_id="eff-001",
            approval_id="apr-001",
        )
        # Should not raise.
        display_event(ev)


# ── Live-stack SSE tests ────────────────────────────────────────────────────


def proxy_url() -> str:
    """Return the proxy base URL from the environment (default ``http://localhost:8080``)."""
    return os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


async def _proxy_healthy() -> bool:
    """Check whether the UndoLog proxy is reachable."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{proxy_url()}/health", timeout=5.0)
            return resp.status_code == 200
    except httpx.RequestError:
        return False


class TestLiveSSEStream:
    """Live-stack tests for the proxy SSE endpoint.

    Requires the UndoLog stack (``docker compose up -d``) and runs only in the
    e2e workflow; the unit CI run deselects the ``integration`` marker.
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_live_events_stream_receives_intercept_frame(self) -> None:
        """A live /events subscription receives a real effect frame."""
        if not await _proxy_healthy():
            pytest.skip("UndoLog stack not running (docker compose up -d)")
        api_key_val = os.environ.get("UNDOLOG_API_KEY")
        assert api_key_val, "UNDOLOG_API_KEY not set"

        session_id = str(uuid.uuid4())
        got_types: list[str] = []
        connection_ready = asyncio.Event()
        frame_received = asyncio.Event()

        async def _consume() -> None:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as stream_client:
                async with stream_client.stream(
                    "GET",
                    f"{proxy_url()}/events",
                    headers={"X-Api-Key": api_key_val},
                ) as resp:
                    content_type = resp.headers.get("content-type", "")
                    if resp.status_code != 200 or not content_type.startswith("text/event-stream"):
                        raise AssertionError(
                            f"expected 200 text/event-stream, got {resp.status_code} {content_type!r}"
                        )
                    connection_ready.set()

                    event_type = ""
                    data_lines: list[str] = []
                    async for line in resp.aiter_lines():
                        stripped = line.strip()
                        if stripped == ": ping":
                            continue
                        if stripped.startswith("event:"):
                            event_type = stripped[len("event:") :].strip()
                            continue
                        if stripped.startswith("data:"):
                            data_lines.append(stripped[len("data:") :].strip())
                            continue
                        if stripped == "" and event_type and data_lines:
                            data = json.loads("".join(data_lines))
                            if data.get("session_id") == session_id:
                                got_types.append(event_type)
                                if event_type == "effect_intercepted":
                                    frame_received.set()
                            event_type = ""
                            data_lines = []

        consume_task = asyncio.create_task(_consume())
        try:
            try:
                await asyncio.wait_for(connection_ready.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pytest.fail("SSE stream did not open")

            # Let the proxy register the subscription before triggering a call.
            await asyncio.sleep(1.0)
            async with httpx.AsyncClient() as post_client:
                resp = await post_client.post(
                    f"{proxy_url()}/mcp/tool_call",
                    json={
                        "session_id": session_id,
                        "tool_name": "charge_payment",
                        "step_index": 1,
                        "args": {"amount": 100, "currency": "USD"},
                    },
                    headers={
                        "X-Api-Key": api_key_val,
                        "Content-Type": "application/json",
                    },
                )
            assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"

            try:
                await asyncio.wait_for(frame_received.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pytest.fail(f"no effect_intercepted frame received; got {got_types!r}")
        finally:
            if not consume_task.done():
                consume_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await consume_task
            else:
                exc = consume_task.exception()
                if exc is not None:
                    raise exc
