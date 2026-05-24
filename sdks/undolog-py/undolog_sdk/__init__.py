"""UndoLog Python SDK - effect-tracking and exactly-once execution for LLM tools.

The SDK works with any async-Python orchestration framework.

Key exports:
    ``undolog_tool``
        Decorator that wraps an async function with UndoLog interception.
    ``UndoLogClient``
        Async HTTP client for the UndoLog MCP proxy.
    ``ToolTier``
        Enum classifying a tool's execution behaviour (Safe, Compensable, Irreversible).
"""

from undolog_sdk.client import UndoLogClient
from undolog_sdk.decorators import AwaitingApprovalError, undolog_tool
from undolog_sdk.tier import CompensationDescriptor, ToolTier

__all__ = [
    "undolog_tool",
    "UndoLogClient",
    "ToolTier",
    "CompensationDescriptor",
    "AwaitingApprovalError",
]
