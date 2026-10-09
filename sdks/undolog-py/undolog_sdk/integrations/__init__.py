"""Framework integrations for the UndoLog SDK.

Each module adapts one orchestration framework to the SDK with a
single wrapper call. Integrations are duck-typed: the SDK takes no
framework dependency, so a module works with the objects the caller
passes and fails with ``AttributeError`` when they lack the interface
it needs.
"""

from __future__ import annotations

from undolog_sdk.integrations.crewai import WrappedCrew, wrap_crewai
from undolog_sdk.integrations.langgraph import (
    WrappedGraph,
    wrap_langgraph,
    wrap_tool,
    wrap_tools,
)
from undolog_sdk.integrations.llamaindex import WrappedIndex, wrap_llamaindex
from undolog_sdk.integrations.semantic_kernel import (
    WrappedKernel,
    wrap_semantic_kernel,
)

__all__ = [
    "WrappedCrew",
    "WrappedGraph",
    "WrappedIndex",
    "WrappedKernel",
    "wrap_crewai",
    "wrap_langgraph",
    "wrap_llamaindex",
    "wrap_semantic_kernel",
    "wrap_tool",
    "wrap_tools",
]
