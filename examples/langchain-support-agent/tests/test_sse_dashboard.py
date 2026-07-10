"""Tests for the SSE dashboard consumer module.

Tests cover SSE wire format parsing, event dispatch, heartbeat
handling, and malformed input resilience.
"""

from __future__ import annotations

import json
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
