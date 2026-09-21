"""Context-var session injection for the UndoLog Python SDK.

Provides ``run_with_session`` as an async context manager that sets a
``contextvars.ContextVar`` holding the current ``UndoLogSession``.
Decorated tools can then resolve the session from the context var
instead of requiring an explicit ``_session`` keyword argument.

Usage::

    from undolog_sdk import undolog_tool, ToolTier, run_with_session
    from undolog_sdk.session import UndoLogSession

    @undolog_tool(tier=ToolTier.SAFE)
    async def search_web(query: str) -> str:
        return f"results for {query}"

    async with UndoLogSession(org_id="org-abc") as session:
        async with run_with_session(session):
            result = await search_web(query="hello")
"""

from __future__ import annotations

import contextvars
import sys
import types

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

from undolog_sdk.session import UndoLogSession

_session_var: contextvars.ContextVar[UndoLogSession | None] = contextvars.ContextVar(
    "undolog_session", default=None
)


def get_current_session() -> UndoLogSession | None:
    """Return the current ``UndoLogSession`` from the context var, or ``None``.

    Returns:
        The session set by ``run_with_session``, or ``None`` if no
        session is active in the current context.
    """
    return _session_var.get()


def require_current_session() -> UndoLogSession:
    """Return the current ``UndoLogSession`` or raise ``RuntimeError``.

    Returns:
        The session set by ``run_with_session``.

    Raises:
        RuntimeError: If no session is active in the current context.
    """
    session = _session_var.get()
    if session is None:
        raise RuntimeError(
            "No UndoLog session in context. "
            "Wrap your code with `async with run_with_session(session):` "
            "or pass _session=session explicitly."
        )
    return session


class _RunWithSession:
    """Async context manager that sets the session context var.

    ``_RunWithSession`` is not used directly; use ``run_with_session``
    which returns an instance of this class.
    """

    def __init__(self, session: UndoLogSession) -> None:
        self._session = session
        self._token: contextvars.Token[UndoLogSession | None] | None = None

    async def __aenter__(self) -> Self:
        self._token = _session_var.set(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._token is not None:
            _session_var.reset(self._token)
            self._token = None


def run_with_session(session: UndoLogSession) -> _RunWithSession:
    """Set ``session`` as the current context-var session.

    Use as an async context manager::

        async with run_with_session(session):
            await my_tool()

    The session is available inside the block via ``get_current_session()``
    or ``require_current_session()``. Decorated tools that do not receive
    an explicit ``_session`` keyword argument will resolve the session
    from this context var.

    Args:
        session: The ``UndoLogSession`` to set as current.

    Returns:
        An async context manager that sets and resets the context var.
    """
    return _RunWithSession(session)
