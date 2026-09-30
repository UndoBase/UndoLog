"""Tests for the ``@undolog_tool`` decorator.

Verifies:
    - Safe tier bypasses the proxy entirely
    - Compensable calls intercept, then commit on success / fail on error
    - Replay returns the unwrapped cached result without executing the
      function body, matching the Execute return shape
    - AwaitingApproval raises ``AwaitingApprovalError`` without execution
    - Step index increments correctly
    - Missing session raises ``RuntimeError``
    - Network errors (timeouts, connection refused) propagate correctly
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from undolog_sdk import AwaitingApprovalError, ToolTier, undolog_tool
from undolog_sdk.client import InterceptResponse, UndoLogClient
from undolog_sdk.context import run_with_session
from undolog_sdk.errors import (
    AuthenticationError,
    ConnectionError,
    ServerError,
    TimeoutError,
)
from undolog_sdk.session import UndoLogSession
from undolog_sdk.tier import CompensationDescriptor


@pytest.fixture
def session() -> UndoLogSession:
    return UndoLogSession(org_id="test-org")


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    return client


# ── Safe tier ─────────────────────────────────────────────────────────────


class TestSafeTier:
    """Safe tools bypass the proxy entirely."""

    async def test_bypasses_proxy(self, session: UndoLogSession) -> None:
        called = False

        @undolog_tool(tier=ToolTier.SAFE)
        async def read_data() -> str:
            nonlocal called
            called = True
            return "data"

        result = await read_data(_session=session)
        assert result == "data"
        assert called is True

    async def test_passes_through_args(self, session: UndoLogSession) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = await greet("Alice", _session=session)
        assert result == "Hello, Alice!"

    async def test_step_not_incremented(self, session: UndoLogSession) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def noop() -> str:
            return "ok"

        await noop(_session=session)
        assert session._step_index == 0


# ── Compensable tier - Execute outcome ────────────────────────────────────


class TestCompensableExecute:
    """Compensable tools call intercept then commit or fail."""

    async def test_calls_intercept_and_commit(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute",
            effect_id="eff-123",
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def create_item(name: str) -> dict[str, Any]:
            return {"id": 1, "name": name}

        result = await create_item("widget", _session=session)
        assert result == {"id": 1, "name": "widget"}

        mock_client.intercept.assert_awaited_once()
        mock_client.commit.assert_awaited_once_with(
            org_id="test-org",
            session_id=session.session_id,
            effect_id="eff-123",
            result={"success": True, "output": {"id": 1, "name": "widget"}},
        )
        mock_client.fail.assert_not_awaited()

    async def test_calls_fail_on_error(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute",
            effect_id="eff-456",
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def failing_tool() -> str:
            raise ValueError("something went wrong")

        with pytest.raises(ValueError, match="something went wrong"):
            await failing_tool(_session=session)

        mock_client.fail.assert_awaited_once_with(
            org_id="test-org",
            session_id=session.session_id,
            effect_id="eff-456",
            error="something went wrong",
        )
        mock_client.commit.assert_not_awaited()

    async def test_step_increments(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute",
            effect_id="eff-1",
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def step_tool() -> str:
            return "ok"

        await step_tool(_session=session)
        assert session._step_index == 1

    async def test_missing_compensation_raises(self) -> None:
        with pytest.raises(ValueError, match="compensation descriptor"):

            @undolog_tool(tier=ToolTier.COMPENSABLE)
            async def bad_tool() -> None:
                pass


# ── Replay outcome ────────────────────────────────────────────────────────


class TestReplay:
    """Replay returns cached result without executing the function body."""

    async def test_returns_cached_result_without_execution(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-replay",
            cached_result={"success": True, "output": "cached-result"},
        )

        executed = False

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def search_tool(query: str) -> str:
            nonlocal executed
            executed = True
            return f"live-{query}"

        result = await search_tool("test", _session=session)
        assert executed is False
        assert result == "cached-result"
        mock_client.intercept.assert_awaited_once()

    async def test_step_increments_on_replay(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-replay",
            cached_result={"success": True, "output": "old"},
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def replay_tool() -> str:
            return "new"

        await replay_tool(_session=session)
        assert session._step_index == 1


class TestReplayUnwrap:
    """Replay unwraps the ToolResult envelope to the natural return type.

    Both Execute and Replay must return the tool's natural return type:
    the ``{"success", "output", "error", "duration_ms"}`` envelope is a
    transport detail and never leaks to the caller.
    """

    async def test_replay_and_execute_return_same_shape(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        """The same tool returns an identical shape on both paths."""

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def lookup(key: str) -> dict[str, int]:
            return {"count": 1}

        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute", effect_id="eff-1"
        )
        executed = await lookup("k", _session=session)

        mock_client.intercept.reset_mock()
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-1",
            cached_result={"success": True, "output": executed},
        )
        replayed = await lookup("k", _session=session)

        assert replayed == executed == {"count": 1}

    async def test_envelope_with_output_key_is_unwrapped(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        """A full envelope: the value under ``output`` is returned."""
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-2",
            cached_result={
                "success": True,
                "output": {"id": 7, "name": "widget"},
                "error": None,
                "duration_ms": 12,
            },
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def fetch() -> dict[str, Any]:
            raise AssertionError("function body must not execute on Replay")

        assert await fetch(_session=session) == {"id": 7, "name": "widget"}

    async def test_non_envelope_dict_passes_through(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        """A cached dict without an ``output`` key is returned as-is."""
        raw = {"custom": True, "nested": {"a": 1}}
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay", effect_id="eff-3", cached_result=raw
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def legacy_tool() -> dict[str, Any]:
            raise AssertionError("function body must not execute on Replay")

        assert await legacy_tool(_session=session) is raw

    async def test_none_cached_result_passes_through(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        """A Replay response with no cached value returns ``None``."""
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay", effect_id="eff-4", cached_result=None
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def ghost() -> str:
            raise AssertionError("function body must not execute on Replay")

        assert await ghost(_session=session) is None

    async def test_tool_returning_none_is_distinguishable_from_missing_cache(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        """An explicit ``output: None`` envelope still unwraps to ``None``."""
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-5",
            cached_result={"success": True, "output": None},
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def nullable() -> None:
            raise AssertionError("function body must not execute on Replay")

        assert await nullable(_session=session) is None

    async def test_string_result_unwraps_to_string(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        """Non-dict tool results survive the envelope round-trip."""
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-6",
            cached_result={"success": True, "output": "plain-text"},
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def echo() -> str:
            raise AssertionError("function body must not execute on Replay")

        assert await echo(_session=session) == "plain-text"

    async def test_context_var_session_replay_unwraps(
        self, mock_client: AsyncMock
    ) -> None:
        """Unwrapping behaves identically with context-var session injection."""
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="eff-7",
            cached_result={"success": True, "output": 42},
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=mock_client,
        )
        async def counter() -> int:
            raise AssertionError("function body must not execute on Replay")

        async with UndoLogSession(org_id="org-abc") as ctx_session:
            async with run_with_session(ctx_session):
                assert await counter() == 42


# ── AwaitingApproval outcome ──────────────────────────────────────────────


class TestAwaitingApproval:
    """AwaitingApproval outcome raises without executing the function body."""

    async def test_raises_without_execution(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="AwaitingApproval",
            approval_id="apr-999",
        )

        executed = False

        @undolog_tool(
            tier=ToolTier.IRREVERSIBLE,
            client=mock_client,
        )
        async def delete_db(db_name: str) -> dict[str, Any]:
            nonlocal executed
            executed = True
            return {"deleted": db_name}

        with pytest.raises(AwaitingApprovalError) as exc:
            await delete_db("prod", _session=session)

        assert exc.value.approval_id == "apr-999"
        assert executed is False

    async def test_step_increments_on_approval(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="AwaitingApproval",
            approval_id="apr-888",
        )

        @undolog_tool(
            tier=ToolTier.IRREVERSIBLE,
            client=mock_client,
        )
        async def dangerous_tool() -> str:
            return "done"

        with pytest.raises(AwaitingApprovalError):
            await dangerous_tool(_session=session)
        assert session._step_index == 1


# ── Session requirements ──────────────────────────────────────────────────


class TestSessionRequired:
    """Missing session parameter raises RuntimeError."""

    async def test_missing_session_raises(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def needs_session() -> str:
            return "ok"

        with pytest.raises(RuntimeError, match="requires a session"):
            await needs_session()

    async def test_custom_session_param(self, session: UndoLogSession) -> None:
        @undolog_tool(tier=ToolTier.SAFE, session_param="ctx")
        async def custom_param() -> str:
            return "ok"

        result = await custom_param(ctx=session)
        assert result == "ok"


# ── Network error simulation ──────────────────────────────────────────────


class TestNetworkErrors:
    """Network-level errors propagate correctly through the decorator.

    Uses ``httpx.MockTransport`` to simulate transport-layer failures
    (timeouts, connection refused) and HTTP error codes, exercising the
    real ``UndoLogClient`` code paths instead of mocking at the client
    level.
    """

    async def test_intercept_connect_error(self, session: UndoLogSession) -> None:
        """ConnectError during intercept wraps as ConnectionError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(_handler)
        http_client = httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8080"
        )
        client = UndoLogClient(http_client=http_client)

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=client,
        )
        async def my_tool() -> str:
            return "ok"

        with pytest.raises(ConnectionError):
            await my_tool(_session=session)

    async def test_intercept_read_timeout(self, session: UndoLogSession) -> None:
        """ReadTimeout during intercept wraps as TimeoutError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("request timed out", request=request)

        transport = httpx.MockTransport(_handler)
        http_client = httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8080"
        )
        client = UndoLogClient(http_client=http_client)

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=client,
        )
        async def my_tool() -> str:
            return "ok"

        with pytest.raises(TimeoutError):
            await my_tool(_session=session)

    async def test_intercept_http_500(self, session: UndoLogSession) -> None:
        """HTTP 500 from the proxy wraps as ServerError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

        transport = httpx.MockTransport(_handler)
        http_client = httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8080"
        )
        client = UndoLogClient(http_client=http_client)

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=client,
        )
        async def my_tool() -> str:
            return "ok"

        with pytest.raises(ServerError):
            await my_tool(_session=session)

    async def test_intercept_http_401(self, session: UndoLogSession) -> None:
        """HTTP 401 from the proxy wraps as AuthenticationError."""

        async def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        transport = httpx.MockTransport(_handler)
        http_client = httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8080"
        )
        client = UndoLogClient(http_client=http_client)

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=client,
        )
        async def my_tool() -> str:
            return "ok"

        with pytest.raises(AuthenticationError):
            await my_tool(_session=session)

    async def test_commit_connect_error(self, session: UndoLogSession) -> None:
        """ConnectError during commit wraps as ConnectionError."""
        call_count: int = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    200, json={"status": "executed", "effect_id": "eff-1"}
                )
            raise httpx.ConnectError("commit connection refused")

        transport = httpx.MockTransport(_handler)
        http_client = httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8080"
        )
        client = UndoLogClient(http_client=http_client)

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=client,
        )
        async def my_tool() -> str:
            return "ok"

        with pytest.raises(ConnectionError):
            await my_tool(_session=session)

    async def test_fail_connect_error(self, session: UndoLogSession) -> None:
        """ConnectError during fail preserves the original function error.

        When the function body raises and the subsequent ``fail()`` call also
        fails, the original exception is preserved (not the network error).
        """
        call_count: int = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    200, json={"status": "executed", "effect_id": "eff-2"}
                )
            raise httpx.ConnectError("fail connection refused")

        transport = httpx.MockTransport(_handler)
        http_client = httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8080"
        )
        client = UndoLogClient(http_client=http_client)

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_test"),
            client=client,
        )
        async def failing_tool() -> str:
            raise ValueError("tool error")

        with pytest.raises(ValueError, match="tool error"):
            await failing_tool(_session=session)
