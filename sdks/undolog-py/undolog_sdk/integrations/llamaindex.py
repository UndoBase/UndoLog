"""LlamaIndex auto-instrumentation for the UndoLog SDK.

``wrap_llamaindex`` runs an index's ``aquery`` inside an
``UndoLogSession``, so tool calls made through the index reach the
proxy: every call is journaled and eligible for replay or approval.

Design notes:
    *   **Duck-typed, no LlamaIndex import.** The facade needs
        ``aquery`` and ``tools``; attributes it does not define
        resolve on the index.
    *   **Only ``aquery`` opens a session and wraps the tools.** The
        other async entry points (``achat``, ``astream``, ``arun``)
        get neither, so an instrumented non-SAFE tool raises
        ``RuntimeError`` for the missing session; open
        ``run_with_session`` around one of them yourself.
    *   **Tools are instrumented at invocation.** The index's tool list
        is reassigned to instrumented copies before each query. The
        wrap is idempotent, so a second query neither re-wraps nor
        journals a call twice, and every tool is accepted before the
        list is reassigned, so a rejected tool leaves the index
        untouched.
    *   **Approvals propagate.** ``AwaitingApprovalError`` escapes
        ``aquery`` with the run's identity already recorded on the
        facade, which is the flow ``docs/guides/integrating-llamaindex.md``
        documents. The caller resolves the approval and queries again
        with ``session_id``, which restarts the counter so steps that
        already completed replay instead of running again.
    *   **The session travels through the context var.** Tools called
        inside ``aquery`` resolve it from ``run_with_session``. Only
        async tools are prepared, because ``undolog_tool`` awaits its
        target: a sync tool would fail at its first call, so it is
        rejected instead.

Example::

    from undolog_sdk.integrations import wrap_llamaindex

    index = wrap_llamaindex(index, org_id="org_demo")
    result = await index.aquery(prompt)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from undolog_sdk import (
    AwaitingApprovalError,
    ToolTier,
    UndoLogClient,
    UndoLogSession,
)
from undolog_sdk.context import run_with_session
from undolog_sdk.integrations._instrument import require_async_tool

# wrap_tools is the shared instrumenter: its container rules (a
# callable, or an object exposing ``coroutine``) are the surface the
# LlamaIndex FunctionTool meets, so this module calls it rather than
# repeating them.
from undolog_sdk.integrations.langgraph import wrap_tools

log = logging.getLogger(__name__)

INPUT_SESSION_KEY = "session_id"
"""``aquery`` keyword carrying the stable UndoLog session id."""

INPUT_STEP_KEY = "undolog_step_index"
"""``aquery`` keyword carrying the live step counter."""


def _prepare_index(
    index: Any,
    tiers: dict[str, ToolTier],
    compensations: dict[str, str],
    client: UndoLogClient | None,
) -> None:
    """Instrument every tool on ``index``, in place, before it queries.

    Parameters:
        index: The index to prepare. Must expose ``tools``.
        tiers: Per-tool tier overrides keyed by tool name.
        compensations: Per-tool compensation registry names keyed by
            tool name.
        client: Optional explicit ``UndoLogClient``.

    Raises:
        AttributeError: If the index does not expose ``tools``. The
            instrumented copies are assigned back to it, so an object
            that holds its tools elsewhere is refused rather than
            queried with tools the wrapper never reached.
        ValueError: If a tool cannot be instrumented. Every tool is
            accepted and wrapped before the list is reassigned, so a
            failure here leaves the index exactly as it was.
    """
    if not hasattr(index, "tools"):
        raise AttributeError(
            "wrap_llamaindex needs the index to expose 'tools': the "
            "instrumented copies are assigned back to it before each "
            "query. If your agent holds them elsewhere, decorate the "
            "tools with @undolog_tool and open run_with_session around "
            "the query yourself."
        )
    tools = list(index.tools or [])
    for tool in tools:
        require_async_tool(tool, "wrap_llamaindex", "FunctionTool")
    index.tools = wrap_tools(
        tools, tiers=tiers, compensations=compensations, client=client
    )


class WrappedIndex:
    """Index facade whose ``aquery`` runs inside an UndoLog session.

    The facade adds ``aquery`` and the UndoLog run properties; every
    other attribute resolves on the wrapped index. ``aquery``
    instruments the index's tools, creates or resumes an
    ``UndoLogSession``, runs the query inside ``run_with_session`` so
    wrapped tools resolve the session from the context var, and records
    the run on the facade. Only ``aquery`` opens a session.
    """

    def __init__(
        self,
        index: Any,
        org_id: str,
        tiers: dict[str, ToolTier],
        compensations: dict[str, str],
        client: UndoLogClient | None,
    ) -> None:
        """Bind the facade to an index.

        Parameters:
            index: Any object exposing ``aquery`` and ``tools``.
            org_id: Organisation identifier for new sessions.
            tiers: Per-tool tier overrides keyed by tool name.
            compensations: Per-tool compensation registry names keyed
                by tool name.
            client: Optional explicit ``UndoLogClient``.
        """
        self._index = index
        self._org_id = org_id
        self._tiers = tiers
        self._compensations = compensations
        self._client = client
        self._session_id: str | None = None
        self._step_index: int = 0
        self._awaiting_approval: bool = False
        self._approval_request: dict[str, Any] | None = None

    @property
    def org_id(self) -> str:
        """Organisation identifier used for new sessions."""
        return self._org_id

    @property
    def session_id(self) -> str | None:
        """Session id of the most recent run, or ``None`` before the first.

        Read this after an ``AwaitingApprovalError`` to resume the same
        journal once the approval resolves.
        """
        return self._session_id

    @property
    def step_index(self) -> int:
        """Step progress reached by the most recent run.

        Passing this back as ``undolog_step_index`` continues the
        counter; omit it on a retried run to replay the steps already
        journaled.
        """
        return self._step_index

    @property
    def awaiting_approval(self) -> bool:
        """Whether the most recent run stopped waiting for a human."""
        return self._awaiting_approval

    @property
    def approval_request(self) -> dict[str, Any] | None:
        """Pending approval details, or ``None`` when nothing is pending."""
        return self._approval_request

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the wrapped index.

        Only called for attributes not found on the facade itself.
        Underscore-prefixed lookups are never delegated, which keeps
        ``self._index`` access safe before the attribute exists.

        Raises:
            AttributeError: If the attribute is private or the wrapped
                index lacks it.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._index, name)

    async def aquery(self, query: Any, **kwargs: Any) -> Any:
        """Query the index inside an UndoLog session.

        Pass ``session_id`` to resume that journal. On a retry, leave
        ``undolog_step_index`` out: the counter restarts, so each call
        lands on the step it already journaled and the engine replays
        it rather than running it again. Pass ``undolog_step_index`` as
        well to continue past those steps. Both keywords are removed
        before the index sees them, because they are UndoLog
        bookkeeping rather than query options.

        Parameters:
            query: The query to run, forwarded unchanged.
            **kwargs: Query options forwarded to ``aquery``. May carry
                ``session_id`` to resume a journal and
                ``undolog_step_index`` to continue its counter.

        Returns:
            Whatever the index returns, unchanged.

        Raises:
            AttributeError: If the index does not expose ``tools``.
                Raised before the session opens.
            ValueError: If any tool cannot be instrumented. The index
                is left untouched when that happens.
            AwaitingApprovalError: When the proxy suspends an
                irreversible tool. The facade records the run first, so
                ``session_id`` and ``approval_request`` are readable
                from the handler.
        """
        session_id = kwargs.pop(INPUT_SESSION_KEY, None)
        start_step = int(kwargs.pop(INPUT_STEP_KEY, 0) or 0)

        _prepare_index(self._index, self._tiers, self._compensations, self._client)

        async with UndoLogSession(org_id=self._org_id) as session:
            if session_id:
                # Resume the journal the previous run wrote to instead
                # of starting a new one.
                session.session_id = str(session_id)
            session._step_index = start_step
            self._session_id = session.session_id
            self._awaiting_approval = False
            self._approval_request = None
            try:
                async with run_with_session(session):
                    return await self._index.aquery(query, **kwargs)
            except AwaitingApprovalError as exc:
                self._awaiting_approval = True
                self._approval_request = {
                    "approval_id": exc.approval_id,
                    "tool_name": exc.tool_name,
                    "step_index": exc.step_index,
                }
                log.info(
                    "undolog aquery: approval required tool=%s step=%d "
                    "approval_id=%s session=%s",
                    exc.tool_name,
                    exc.step_index,
                    exc.approval_id,
                    session.session_id,
                )
                raise
            finally:
                # Report the progress the run actually reached,
                # including the step that suspended it.
                self._step_index = session._step_index


def wrap_llamaindex(
    index: Any,
    org_id: str | None = None,
    tiers: dict[str, ToolTier] | None = None,
    compensations: dict[str, str] | None = None,
    client: UndoLogClient | None = None,
) -> WrappedIndex:
    """Run a LlamaIndex query inside an UndoLog session, in one call.

    Parameters:
        index: Anything exposing ``aquery`` and ``tools``, such as an
            agent built from LlamaIndex ``FunctionTool`` objects.
        org_id: Organisation identifier for sessions. Defaults to the
            ``UNDOLOG_ORG_ID`` environment variable, then ``org_demo``.
        tiers: Per-tool tier overrides keyed by tool name. Tools not
            listed default to ``COMPENSABLE``.
        compensations: Per-tool compensation registry names keyed by
            tool name. Required for any ``COMPENSABLE`` tool that is
            not already decorated with ``@undolog_tool``.
        client: Optional explicit ``UndoLogClient``.

    Returns:
        A ``WrappedIndex`` facade. The original index is not replaced;
        its tool list is reassigned to the instrumented copies at each
        query.

    Raises:
        AttributeError: At the first ``aquery``, if the index does not
            expose ``tools``.
        ValueError: At the first ``aquery``, if a tool cannot be
            instrumented.

    Note:
        Tools already decorated with ``@undolog_tool`` come back
        untouched, so an index built from decorated tools needs no tier
        or compensation mapping.

    Example:
        Single-line integration::

            index = wrap_llamaindex(index, org_id="org_demo")
            result = await index.aquery(prompt)
    """
    resolved_org = org_id or os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    return WrappedIndex(
        index,
        resolved_org,
        tiers or {},
        compensations or {},
        client,
    )
