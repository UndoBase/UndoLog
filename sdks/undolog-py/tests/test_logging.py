"""Tests for structured logging at SDK lifecycle points.

Verifies that the client, decorator, and context layers emit log records
at the expected levels with structured context fields.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from undolog_sdk import ToolTier, undolog_tool
from undolog_sdk.context import run_with_session
from undolog_sdk.decorators import AwaitingApprovalError
from undolog_sdk.logging import get_sdk_logger
from undolog_sdk.session import UndoLogSession


class TestGetSdkLogger:
    """Tests for the get_sdk_logger helper."""

    def test_returns_undolog_sdk_namespace(self) -> None:
        logger = get_sdk_logger()
        assert logger.name == "undolog_sdk"

    def test_returns_same_instance(self) -> None:
        logger_a = get_sdk_logger()
        logger_b = get_sdk_logger()
        assert logger_a is logger_b


class TestContextSessionLogging:
    """Tests that run_with_session emits session_start/session_end."""

    def test_session_start_and_end(self, caplog: pytest.LogCaptureFixture) -> None:
        session = UndoLogSession(org_id="org")

        async def _run() -> None:
            async with run_with_session(session):
                pass

        with caplog.at_level(logging.INFO, logger="undolog_sdk.context"):
            asyncio.run(_run())

        records = [r for r in caplog.records if "session_" in r.message]
        assert len(records) == 2
        assert "session_start" in records[0].message
        assert "session_end" in records[1].message


class TestDecoratorLogging:
    """Tests that the decorator emits logging at lifecycle points."""

    def test_safe_tier_emits_debug_log(self, caplog: pytest.LogCaptureFixture) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def safe_tool(x: int) -> int:
            return x * 2

        session = UndoLogSession(org_id="org")
        with caplog.at_level(logging.DEBUG, logger="undolog_sdk.decorators"):
            asyncio.run(_call_tool(run_with_session(session), safe_tool, 5))
        assert any("tool_execute" in r.message for r in caplog.records)

    def test_execute_outcome_logs_commit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @undolog_tool(tier=ToolTier.IRREVERSIBLE)
        async def irreversible_tool(x: int) -> int:
            return x + 1

        session = UndoLogSession(org_id="org")
        mock_client = AsyncMock()
        mock_client.intercept = AsyncMock(
            return_value=MagicMock(outcome="Execute", effect_id="eff-1")
        )
        mock_client.commit = AsyncMock(return_value={})

        with caplog.at_level(logging.INFO, logger="undolog_sdk.decorators"):
            asyncio.run(
                _call_tool_with_client(
                    run_with_session(session),
                    irreversible_tool,
                    5,
                    client_override=mock_client,
                )
            )
        assert any("tool_committed" in r.message for r in caplog.records)

    def test_replay_outcome_logs_replay(self, caplog: pytest.LogCaptureFixture) -> None:
        @undolog_tool(tier=ToolTier.IRREVERSIBLE)
        async def irreversible_tool(x: int) -> int:
            return x + 1

        session = UndoLogSession(org_id="org")
        mock_client = AsyncMock()
        mock_client.intercept = AsyncMock(
            return_value=MagicMock(outcome="Replay", cached_result={"cached": True})
        )

        with caplog.at_level(logging.INFO, logger="undolog_sdk.decorators"):
            asyncio.run(
                _call_tool_with_client(
                    run_with_session(session),
                    irreversible_tool,
                    5,
                    client_override=mock_client,
                )
            )
        assert any("tool_replay" in r.message for r in caplog.records)

    def test_failure_outcome_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @undolog_tool(tier=ToolTier.IRREVERSIBLE)
        async def failing_tool(x: int) -> int:
            raise ValueError("boom")

        session = UndoLogSession(org_id="org")
        mock_client = AsyncMock()
        mock_client.intercept = AsyncMock(
            return_value=MagicMock(outcome="Execute", effect_id="eff-1")
        )
        mock_client.fail = AsyncMock(return_value={})

        with caplog.at_level(logging.WARNING, logger="undolog_sdk.decorators"):
            with pytest.raises(ValueError, match="boom"):
                asyncio.run(
                    _call_tool_with_client(
                        run_with_session(session),
                        failing_tool,
                        5,
                        client_override=mock_client,
                    )
                )
        assert any("tool_failed" in r.message for r in caplog.records)

    def test_approval_outcome_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @undolog_tool(tier=ToolTier.IRREVERSIBLE)
        async def needs_approval(x: int) -> int:
            return x + 1

        session = UndoLogSession(org_id="org")
        mock_client = AsyncMock()
        mock_client.intercept = AsyncMock(
            return_value=MagicMock(outcome="AwaitingApproval", approval_id="appr-1")
        )

        with caplog.at_level(logging.WARNING, logger="undolog_sdk.decorators"):
            with pytest.raises(AwaitingApprovalError):
                asyncio.run(
                    _call_tool_with_client(
                        run_with_session(session),
                        needs_approval,
                        5,
                        client_override=mock_client,
                    )
                )
        assert any("approval_required" in r.message for r in caplog.records)


async def _call_tool(cm: Any, func: Any, *args: Any) -> Any:
    async with cm:
        return await func(*args)


async def _call_tool_with_client(
    cm: Any, func: Any, *args: Any, client_override: Any
) -> Any:
    async with cm:
        with patch(
            "undolog_sdk.decorators._get_default_client",
            return_value=client_override,
        ):
            return await func(*args)
