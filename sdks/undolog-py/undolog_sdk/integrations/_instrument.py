"""Shared instrumentation helpers for the integration wrappers.

Each wrapper adapts one framework, so its public surface stays in its own
module. The rules that do not depend on a framework, such as whether a tool
can be intercepted at all, live here so the wrappers cannot drift apart.
"""

from __future__ import annotations

import inspect
from typing import Any


def require_async_tool(tool: Any, wrapper: str, container: str | None) -> None:
    """Reject a tool that ``undolog_tool`` could not actually intercept.

    ``undolog_tool`` awaits the callable it wraps, so a sync tool would fail
    at its first call and the wrap would have promised interception that never
    happens.

    Parameters:
        tool: A tool: a callable, or an object exposing a ``coroutine``
            attribute.
        wrapper: Name of the wrapping integration, used in the error message
            so the caller knows which call to fix.
        container: Name of the framework's tool object, for a hint about
            exposing an async ``coroutine``. ``None`` for a wrapper that
            accepts callables only, where the hint would suggest a shape it
            rejects.

    Raises:
        ValueError: If the callable that would run is not a coroutine
            function.
    """
    inner = getattr(tool, "coroutine", None)
    target = inner if callable(inner) else tool
    if not callable(target) or inspect.iscoroutinefunction(target):
        return
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool
    hint = (
        f" Use an async function, or a {container} exposing an async 'coroutine'."
        if container
        else " Use an async function."
    )
    raise ValueError(
        f"{wrapper} cannot instrument {name!r}: UndoLog awaits the wrapped "
        f"callable, so the tool must be async.{hint}"
    )
