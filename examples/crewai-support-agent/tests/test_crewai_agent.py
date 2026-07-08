"""Tests for the CrewAI support agent."""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest

pytest.importorskip("langchain_core")


class TestCrewAIAgent:
    """CrewAI agent imports and tool building."""

    def test_import_crashes_without_crewai(self) -> None:
        import crewai_agent  # noqa: F401  -- verify module loads without error

    def test_build_tools_requires_crewai(self) -> None:
        with mock.patch.dict("sys.modules", {"crewai": None, "langchain_core": None}):
            import importlib

            import crewai_agent as ca

            importlib.reload(ca)
            with pytest.raises(RuntimeError, match="crewai and langchain_core"):
                ca._build_lc_tools()

    def test_main_requires_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            from crewai_agent import main

            asyncio.run(main())
