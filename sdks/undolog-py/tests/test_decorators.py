"""Tests for the ``@undolog_tool`` decorator.

Verifies:
    - Safe tier bypasses the proxy entirely
    - Compensable calls intercept, then commit on success / fail on error
    - Replay returns the cached result without executing the function body
    - AwaitingApproval raises ``AwaitingApprovalError`` without execution
    - Step index increments correctly
    - Missing session raises ``RuntimeError``
    - Network errors (timeouts, connection refused) propagate correctly
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from typing import Any

from undolog_sdk import AwaitingApprovalError, ToolTier, undolog_tool
from undolog_sdk.client import InterceptResponse, UndoLogClient
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
        assert session._step_index == 1


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
        assert result == {"success": True, "output": "cached-result"}
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
        """ConnectError during intercept propagates as-is to the caller."""

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

        with pytest.raises(httpx.ConnectError):
            await my_tool(_session=session)

    async def test_intercept_read_timeout(self, session: UndoLogSession) -> None:
        """ReadTimeout during intercept propagates as-is to the caller."""

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

        with pytest.raises(httpx.ReadTimeout):
            await my_tool(_session=session)

    async def test_intercept_http_500(self, session: UndoLogSession) -> None:
        """HTTP 500 from the proxy propagates as HTTPStatusError."""

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

        with pytest.raises(httpx.HTTPStatusError):
            await my_tool(_session=session)

    async def test_intercept_http_401(self, session: UndoLogSession) -> None:
        """HTTP 401 from the proxy propagates as HTTPStatusError."""

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

        with pytest.raises(httpx.HTTPStatusError):
            await my_tool(_session=session)

    async def test_commit_connect_error(self, session: UndoLogSession) -> None:
        """ConnectError during commit after a successful intercept."""
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

        with pytest.raises(httpx.ConnectError):
            await my_tool(_session=session)

    async def test_fail_connect_error(self, session: UndoLogSession) -> None:
        """ConnectError during fail replaces the original function error.

        When the function body raises and the subsequent ``fail()`` call also
        fails, the network error propagates (the original exception is lost).
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

        with pytest.raises(httpx.ConnectError):
            await failing_tool(_session=session)
