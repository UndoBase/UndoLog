"""Framework integrations for the UndoLog SDK.

Each module adapts one orchestration framework to the SDK with a
single wrapper call. Integrations are duck-typed: the SDK takes no
framework dependency, so a module works with the objects the caller
passes and fails with ``AttributeError`` when they lack the interface
it needs.
"""

from __future__ import annotations

from undolog_sdk.integrations.langgraph import (
    WrappedGraph,
    wrap_langgraph,
    wrap_tool,
    wrap_tools,
)

__all__ = [
    "WrappedGraph",
    "wrap_langgraph",
    "wrap_tool",
    "wrap_tools",
]
