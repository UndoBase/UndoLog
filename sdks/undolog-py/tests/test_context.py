"""Tests for context-var session injection.

Covers ``run_with_session``, ``get_current_session``,
``require_current_session``, and decorator integration with context vars.
"""

from __future__ import annotations

import asyncio

import pytest

from undolog_sdk import ToolTier, run_with_session, undolog_tool
from undolog_sdk.context import get_current_session, require_current_session
from undolog_sdk.session import UndoLogSession

# ── get_current_session ──────────────────────────────────────────────────


class TestGetCurrentSession:
    """``get_current_session`` returns the session or ``None``."""

    async def test_returns_none_outside_context(self) -> None:
        assert get_current_session() is None

    async def test_returns_session_inside_context(self) -> None:
        session = UndoLogSession(org_id="org")
        async with run_with_session(session):
            assert get_current_session() is session

    async def test_returns_none_after_context_exits(self) -> None:
        session = UndoLogSession(org_id="org")
        async with run_with_session(session):
            pass
        assert get_current_session() is None


# ── require_current_session ──────────────────────────────────────────────


class TestRequireCurrentSession:
    """``require_current_session`` returns the session or raises."""

    async def test_raises_outside_context(self) -> None:
        with pytest.raises(RuntimeError, match="No UndoLog session"):
            require_current_session()

    async def test_returns_session_inside_context(self) -> None:
        session = UndoLogSession(org_id="org")
        async with run_with_session(session):
            assert require_current_session() is session


# ── run_with_session ─────────────────────────────────────────────────────


class TestRunWithSession:
    """``run_with_session`` sets and resets the context var."""

    async def test_sets_session(self) -> None:
        session = UndoLogSession(org_id="org")
        async with run_with_session(session):
            assert get_current_session() is session

    async def test_resets_on_exit(self) -> None:
        session = UndoLogSession(org_id="org")
        async with run_with_session(session):
            pass
        assert get_current_session() is None

    async def test_resets_on_exception(self) -> None:
        session = UndoLogSession(org_id="org")
        try:
            async with run_with_session(session):
                raise ValueError("test")
        except ValueError:
            pass
        assert get_current_session() is None

    async def test_nested_contexts_are_independent(self) -> None:
        outer = UndoLogSession(org_id="outer")
        inner = UndoLogSession(org_id="inner")
        async with run_with_session(outer):
            assert get_current_session() is outer
            async with run_with_session(inner):
                assert get_current_session() is inner
            assert get_current_session() is outer
        assert get_current_session() is None


# ── Decorator integration ───────────────────────────────────────────────


class TestDecoratorContextVar:
    """Decorator resolves session from context var when not passed explicitly."""

    async def test_safe_tool_with_context_var(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def read_data() -> str:
            return "data"

        session = UndoLogSession(org_id="org")
        async with run_with_session(session):
            result = await read_data()
        assert result == "data"

    async def test_explicit_session_takes_precedence(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def read_data() -> str:
            return "data"

        ctx_session = UndoLogSession(org_id="ctx")
        explicit_session = UndoLogSession(org_id="explicit")
        async with run_with_session(ctx_session):
            result = await read_data(_session=explicit_session)
        assert result == "data"

    async def test_raises_when_no_session_anywhere(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def read_data() -> str:
            return "data"

        with pytest.raises(RuntimeError, match="requires a session"):
            await read_data()

    async def test_concurrent_tasks_have_independent_contexts(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def get_org() -> str:
            session = require_current_session()
            return session.org_id

        session_a = UndoLogSession(org_id="org-a")
        session_b = UndoLogSession(org_id="org-b")

        async def run_in_context(s: UndoLogSession) -> str:
            async with run_with_session(s):
                return await get_org()  # type: ignore[no-any-return]

        results = await asyncio.gather(
            run_in_context(session_a),
            run_in_context(session_b),
        )
        assert list(results) == ["org-a", "org-b"]
