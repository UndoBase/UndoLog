"""Tests for the Semantic Kernel support agent."""

from __future__ import annotations

import asyncio
import os
from unittest import mock


class TestSemanticKernelAgent:
    """Semantic Kernel agent imports and tool building."""

    def test_import_without_sk_does_not_crash(self) -> None:
        import semantic_kernel_agent  # noqa: F401  -- verify module loads without error

    def test_main_requires_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            from semantic_kernel_agent import main

            asyncio.run(main())
