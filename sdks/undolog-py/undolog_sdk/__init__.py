"""UndoLog Python SDK - effect-tracking and exactly-once execution for LLM tools.

The SDK works with any async-Python orchestration framework.

Key exports:
    ``undolog_tool``
        Decorator that wraps an async function with UndoLog interception.
    ``UndoLogClient``
        Async HTTP client for the UndoLog MCP proxy.
    ``UndoLogSession``
        Async context manager that tracks org, session, and step state.
    ``run_with_session``
        Async context manager that sets the session context var.
    ``ToolTier``
        Enum classifying a tool's execution behaviour (Safe, Compensable, Irreversible).
    ``CompensationTestHarness``
        Test harness for compensation functions without a running engine.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from undolog_sdk.client import UndoLogClient
from undolog_sdk.context import (
    get_current_session,
    require_current_session,
    run_with_session,
)
from undolog_sdk.decorators import AwaitingApprovalError, undolog_tool
from undolog_sdk.errors import (
    AuthenticationError,
    ConnectionError,
    ServerError,
    TimeoutError,
    UndoLogError,
)
from undolog_sdk.session import UndoLogSession
from undolog_sdk.test_harness import CompensationTestHarness
from undolog_sdk.tier import CompensationDescriptor, ToolTier

try:
    __version__ = version("undolog-sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "AuthenticationError",
    "AwaitingApprovalError",
    "CompensationDescriptor",
    "CompensationTestHarness",
    "ConnectionError",
    "ServerError",
    "TimeoutError",
    "ToolTier",
    "UndoLogClient",
    "UndoLogError",
    "UndoLogSession",
    "__version__",
    "get_current_session",
    "require_current_session",
    "run_with_session",
    "undolog_tool",
]
