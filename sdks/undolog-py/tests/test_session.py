"""Tests for UndoLogSession.

Covers context manager lifecycle, session identity, step tracking,
and instance independence. Step-index invariants exercised through the
decorator are tested in ``test_state_machine.py``; this file tests the
session object directly.
"""

from __future__ import annotations

import uuid

from undolog_sdk.session import UndoLogSession

# ── Context manager lifecycle ──────────────────────────────────────────────


class TestContextManager:
    """``UndoLogSession`` is an async context manager."""

    async def test_enter_returns_self(self) -> None:
        session = UndoLogSession(org_id="org")
        async with session as s:
            assert s is session

    async def test_exit_is_clean(self) -> None:
        session = UndoLogSession(org_id="org")
        async with session:
            pass
        assert session._step_index == 0

    async def test_exit_preserves_step_state(self) -> None:
        session = UndoLogSession(org_id="org")
        async with session:
            session.next_step()
            session.next_step()
        assert session._step_index == 2


# ── Session identity ───────────────────────────────────────────────────────


class TestSessionIdentity:
    """Session ID is a valid UUID v4, unique per instance, and stable."""

    async def test_session_id_is_valid_uuid(self) -> None:
        session = UndoLogSession(org_id="org")
        parsed = uuid.UUID(session.session_id)
        assert parsed.version == 4

    async def test_session_id_is_stable(self) -> None:
        session = UndoLogSession(org_id="org")
        id1 = session.session_id
        id2 = session.session_id
        assert id1 == id2

    async def test_sessions_have_unique_ids(self) -> None:
        ids = {UndoLogSession(org_id="org").session_id for _ in range(50)}
        assert len(ids) == 50

    async def test_org_id_stored(self) -> None:
        session = UndoLogSession(org_id="my-org")
        assert session.org_id == "my-org"


# ── Step tracking ──────────────────────────────────────────────────────────


class TestStepTracking:
    """Step index starts at 0 and increments via ``next_step()``."""

    async def test_initial_step_is_zero(self) -> None:
        session = UndoLogSession(org_id="org")
        assert session._step_index == 0

    async def test_next_step_returns_1_based(self) -> None:
        session = UndoLogSession(org_id="org")
        assert session.next_step() == 1
        assert session.next_step() == 2
        assert session.next_step() == 3

    async def test_step_is_strictly_monotonic(self) -> None:
        session = UndoLogSession(org_id="org")
        steps = [session.next_step() for _ in range(20)]
        for i in range(1, len(steps)):
            assert steps[i] > steps[i - 1]
