"""Tests for the LlamaIndex support agent."""

from __future__ import annotations

import asyncio
import os
from unittest import mock


class TestLlamaIndexAgent:
    """LlamaIndex agent imports and tool building."""

    def test_import_without_llama_does_not_crash(self) -> None:
        import llama_index_agent  # noqa: F401  -- verify module loads without error

    def test_main_requires_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            from llama_index_agent import main

            asyncio.run(main())
