"""Property-style tests for decorator state transitions.

Verifies that the decorator produces the correct client call pattern for
every (tier, outcome) combination, and that session step_index invariants
hold regardless of execution order.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from undolog_sdk import AwaitingApprovalError, ToolTier, undolog_tool
from undolog_sdk.client import InterceptResponse
from undolog_sdk.session import UndoLogSession
from undolog_sdk.tier import CompensationDescriptor


@pytest.fixture
def session() -> UndoLogSession:
    return UndoLogSession(org_id="test-org")


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    return client


# ── Effect state transitions ────────────────────────────────────────────────


class TestEffectTransitions:
    """Every (tier, outcome) produces the correct client call pattern."""

    async def test_safe_bypasses_proxy(self, session: UndoLogSession) -> None:
        called = False

        @undolog_tool(tier=ToolTier.SAFE)
        async def read() -> str:
            nonlocal called
            called = True
            return "data"

        result = await read(_session=session)
        assert result == "data"
        assert called

    async def test_compensable_execute_calls_commit(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute", effect_id="e1"
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_fn"),
            client=mock_client,
        )
        async def write() -> str:
            return "ok"

        result = await write(_session=session)
        assert result == "ok"
        mock_client.intercept.assert_awaited_once()
        mock_client.commit.assert_awaited_once()
        mock_client.fail.assert_not_awaited()

    async def test_compensable_fail_calls_fail(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute", effect_id="e2"
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_fn"),
            client=mock_client,
        )
        async def failing() -> str:
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await failing(_session=session)
        mock_client.intercept.assert_awaited_once()
        mock_client.commit.assert_not_awaited()
        mock_client.fail.assert_awaited_once()

    async def test_replay_returns_cached(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Replay",
            effect_id="e3",
            cached_result={"success": True, "output": "cached"},
        )

        called = False

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_fn"),
            client=mock_client,
        )
        async def dedup() -> str:
            nonlocal called
            called = True
            return "fresh"

        result = await dedup(_session=session)
        assert result == {"success": True, "output": "cached"}
        assert not called
        mock_client.intercept.assert_awaited_once()
        mock_client.commit.assert_not_awaited()
        mock_client.fail.assert_not_awaited()

    async def test_irreversible_raises_approval(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="AwaitingApproval", effect_id="e4", approval_id="ap-1"
        )

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=mock_client)
        async def dangerous() -> str:
            return "done"

        with pytest.raises(AwaitingApprovalError) as exc:
            await dangerous(_session=session)
        assert exc.value.approval_id == "ap-1"
        mock_client.intercept.assert_awaited_once()
        mock_client.commit.assert_not_awaited()
        mock_client.fail.assert_not_awaited()

    async def test_missing_session_raises_error(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def read() -> str:
            return "data"

        with pytest.raises(RuntimeError, match="requires a session"):
            await read()

    async def test_network_error_propagates(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.side_effect = RuntimeError("connection refused")

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_fn"),
            client=mock_client,
        )
        async def remote() -> str:
            return "ok"

        with pytest.raises(RuntimeError, match="connection refused"):
            await remote(_session=session)


# ── Session step_index invariants ──────────────────────────────────────────


class TestSessionStepInvariants:
    """Step index monotonicity and ordering invariants."""

    async def test_step_starts_at_zero(self) -> None:
        s = UndoLogSession(org_id="test")
        assert s._step_index == 0

    async def test_step_increments_by_one(self, session: UndoLogSession) -> None:
        assert session.next_step() == 1
        assert session.next_step() == 2
        assert session.next_step() == 3
        assert session._step_index == 3

    async def test_safe_tools_increment_step(self, session: UndoLogSession) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def t() -> str:
            return "ok"

        await t(_session=session)
        assert session._step_index == 1

        await t(_session=session)
        assert session._step_index == 2

    async def test_compensable_tools_increment_step(
        self, session: UndoLogSession, mock_client: AsyncMock
    ) -> None:
        mock_client.intercept.return_value = InterceptResponse(
            outcome="Execute", effect_id="e1"
        )

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_fn"),
            client=mock_client,
        )
        async def t() -> str:
            return "ok"

        await t(_session=session)
        assert session._step_index == 1

    async def test_step_is_monotonic(self, session: UndoLogSession) -> None:
        steps = [session.next_step() for _ in range(10)]
        assert steps == list(range(1, 11))
        # Strictly increasing
        for i in range(1, len(steps)):
            assert steps[i] > steps[i - 1]

    async def test_concurrent_sessions_have_independent_steps(self) -> None:
        s1 = UndoLogSession(org_id="a")
        s2 = UndoLogSession(org_id="b")
        s1.next_step()
        s1.next_step()
        s2.next_step()
        assert s1._step_index == 2
        assert s2._step_index == 1


# ── Compensation descriptor invariants ─────────────────────────────────────


class TestCompensationDescriptorInvariants:
    """Compensation descriptor construction and property invariants."""

    def test_new_creates_valid_descriptor(self) -> None:
        d = CompensationDescriptor.new("compensate_send_email")
        assert d.fn_name == "compensate_send_email"
        assert d.args == {}

    def test_new_with_args(self) -> None:
        d = CompensationDescriptor.new("compensate_ticket", args={"ticket_id": "{id}"})
        assert d.fn_name == "compensate_ticket"
        assert d.args == {"ticket_id": "{id}"}

    def test_retry_defaults(self) -> None:
        d = CompensationDescriptor.new("fn")
        assert d.max_retries == 3
        assert d.retry_backoff_ms == 1000

    def test_custom_retry(self) -> None:
        d = CompensationDescriptor(fn_name="fn", max_retries=5, retry_backoff_ms=2000)
        assert d.max_retries == 5
        assert d.retry_backoff_ms == 2000

    def test_descriptor_equality(self) -> None:
        a = CompensationDescriptor(
            fn_name="fn", args={"x": "1"}, max_retries=3, retry_backoff_ms=1000
        )
        b = CompensationDescriptor(
            fn_name="fn", args={"x": "1"}, max_retries=3, retry_backoff_ms=1000
        )
        assert a == b

    def test_descriptor_inequality(self) -> None:
        a = CompensationDescriptor.new("fn_a")
        b = CompensationDescriptor.new("fn_b")
        assert a != b
