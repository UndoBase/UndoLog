"""LlamaIndex auto-instrumentation for the UndoLog SDK.

``wrap_llamaindex`` runs an index's ``aquery`` or agent's ``achat`` inside an
``UndoLogSession``, so tool calls made through the index reach the
proxy: every call is journaled and eligible for replay or approval.

Design notes:
    *   **Duck-typed, no LlamaIndex import.** The facade works with indices
        or agents providing ``aquery`` or ``achat``, and tools through
        ``tools`` or ``tool_specs``; attributes it does not define
        resolve on the wrapped object.
    *   **``aquery`` and ``achat`` open sessions and wrap tools.** ``achat``
        automatically maintains session continuity across multi-turn
        conversations. Other async entry points (``astream``, ``arun``)
        rely on an enclosing ``run_with_session``.
    *   **Tools are instrumented at invocation.** The index's tool list
        is reassigned to instrumented copies before each query. The
        wrap is idempotent, so a second query neither re-wraps nor
        journals a call twice, and every tool is accepted before the
        list is reassigned, so a rejected tool leaves the index
        untouched.
    *   **Approvals propagate.** ``AwaitingApprovalError`` escapes
        ``aquery`` or ``achat`` with the run's identity already recorded on the
        facade, which is the flow ``docs/guides/integrating-llamaindex.md``
        documents. The caller resolves the approval and queries again
        with ``session_id``, which restarts the counter so steps that
        already completed replay instead of running again.
    *   **The session travels through the context var.** Tools called
        inside ``aquery`` or ``achat`` resolve it from ``run_with_session``. Only
        async tools are prepared, because ``undolog_tool`` awaits its
        target: a sync tool would fail at its first call, so it is
        rejected instead.

Example::

    from undolog_sdk.integrations import wrap_llamaindex

    index = wrap_llamaindex(index, org_id="org_demo")
    result = await index.aquery(prompt)
"""

from __future__ import annotations

import copy
import functools
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from undolog_sdk import (
    AwaitingApprovalError,
    ToolTier,
    UndoLogClient,
    UndoLogSession,
)
from undolog_sdk.context import run_with_session
from undolog_sdk.integrations._instrument import require_async_tool
from undolog_sdk.integrations.langgraph import wrap_tool

log = logging.getLogger(__name__)

INPUT_SESSION_KEY = "session_id"
"""``aquery`` keyword carrying the stable UndoLog session id."""

INPUT_STEP_KEY = "undolog_step_index"
"""``aquery`` keyword carrying the live step counter."""


_SAFE_PREFIXES = (
    "get_",
    "read_",
    "fetch_",
    "list_",
    "search_",
    "lookup_",
    "query_",
    "check_",
    "describe_",
    "view_",
    "find_",
    "calculate_",
    "is_",
    "has_",
    "show_",
)

_IRREVERSIBLE_PREFIXES = (
    "delete_",
    "drop_",
    "destroy_",
    "remove_",
    "purge_",
    "terminate_",
    "publish_",
    "execute_",
    "kill_",
    "wipe_",
    "charge_",
    "pay_",
    "transfer_",
    "send_",
)


def classify_tool(tool: Any) -> ToolTier:
    """Classify an un-annotated tool into a ToolTier using name heuristics.

    Read-only operations (get, read, fetch, list, search, query, etc.)
    classify as ``ToolTier.SAFE``.
    Modifying or destructive operations (delete, drop, send, charge, etc.)
    classify as ``ToolTier.IRREVERSIBLE``.
    Other tools default to ``ToolTier.COMPENSABLE``.

    Parameters:
        tool: An async callable or tool object.

    Returns:
        The deduced :class:`ToolTier`.
    """
    existing_tier = getattr(tool, "_undolog_tier", None)
    if isinstance(existing_tier, ToolTier):
        return existing_tier

    name = getattr(tool, "name", None) or getattr(tool, "__name__", "")
    lower_name = str(name).lower()

    for prefix in _SAFE_PREFIXES:
        if lower_name.startswith(prefix):
            return ToolTier.SAFE

    for prefix in _IRREVERSIBLE_PREFIXES:
        if lower_name.startswith(prefix):
            return ToolTier.IRREVERSIBLE

    return ToolTier.COMPENSABLE


def audit_index_tool(
    tool: Callable[..., Awaitable[Any]],
    tier: ToolTier | None = None,
    compensation_name: str | None = None,
    client: UndoLogClient | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap an un-annotated tool, classifying it and logging warnings.

    Parameters:
        tool: Un-annotated async tool callable.
        tier: Explicit ToolTier, or None to deduce via :func:`classify_tool`.
        compensation_name: Optional compensation name.
        client: Optional UndoLogClient.

    Returns:
        Instrumented callable that logs a warning and executes under the
        classified tier.
    """
    resolved_tier = tier or classify_tool(tool)
    name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
    log.warning(
        "Un-annotated tool %r detected; classified as %s",
        name,
        resolved_tier.value,
    )
    wrapped = wrap_tool(
        tool,
        tier=resolved_tier,
        compensation_name=compensation_name,
        client=client,
    )

    @functools.wraps(tool)
    async def audited_index_wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = getattr(tool, "__name__", str(tool))
        log.warning(
            "Un-annotated tool %r invoked within agent runner; classified as %s",
            tool_name,
            resolved_tier.value,
        )
        return await wrapped(*args, **kwargs)

    for attr in (
        "_undolog_tool_name",
        "_undolog_tier",
        "_undolog_compensation",
        "_undolog_client",
    ):
        if hasattr(wrapped, attr):
            setattr(audited_index_wrapper, attr, getattr(wrapped, attr))
    return audited_index_wrapper


def _instrument_index_tool(
    tool: Any,
    tiers: dict[str, ToolTier],
    compensations: dict[str, str],
    client: UndoLogClient | None,
) -> Any:
    """Instrument a single tool object or container."""
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    target = tool
    container: Any = None
    inner = getattr(tool, "coroutine", None)
    if inner is not None and callable(inner):
        target = inner
        container = tool

    if not callable(target) or not name or not hasattr(target, "__name__"):
        raise ValueError(
            f"wrap_llamaindex cannot instrument {name or tool!r}: UndoLog "
            "needs an async callable with a name, or an object exposing "
            "an async 'coroutine' attribute."
        )

    if getattr(target, "_undolog_tool_name", None) is not None:
        return tool

    if name and name in tiers:
        tier = tiers[name]
        instrumented = wrap_tool(
            target,
            tier=tier,
            compensation_name=compensations.get(name),
            client=client,
        )
    else:
        resolved_tier = classify_tool(target)
        instrumented = audit_index_tool(
            target,
            tier=resolved_tier,
            compensation_name=compensations.get(name) if name else None,
            client=client,
        )

    if container is not None:
        replica = copy.copy(tool)
        setattr(replica, "coroutine", instrumented)
        return replica
    return instrumented


def _prepare_index(
    index: Any,
    tiers: dict[str, ToolTier],
    compensations: dict[str, str],
    client: UndoLogClient | None,
) -> None:
    """Instrument every tool on ``index`` or agent, in place, before execution.

    Parameters:
        index: The index or agent to prepare. Must expose ``tools`` or ``tool_specs``.
        tiers: Per-tool tier overrides keyed by tool name.
        compensations: Per-tool compensation registry names keyed by
            tool name.
        client: Optional explicit ``UndoLogClient``.

    Raises:
        AttributeError: If the index does not expose ``tools`` or ``tool_specs``.
        ValueError: If a tool cannot be instrumented.
    """
    has_tools = hasattr(index, "tools")
    has_specs = hasattr(index, "tool_specs")
    if not has_tools and not has_specs:
        raise AttributeError(
            "wrap_llamaindex needs the index or agent to expose 'tools' or "
            "'tool_specs': the instrumented copies are assigned back to it "
            "before each query. If your agent holds them elsewhere, decorate the "
            "tools with @undolog_tool and open run_with_session around "
            "the query yourself."
        )

    tools = list(index.tools or []) if has_tools and index.tools is not None else []
    tool_specs = (
        list(index.tool_specs or [])
        if has_specs and index.tool_specs is not None
        else []
    )

    for tool in tools:
        require_async_tool(tool, "wrap_llamaindex", "FunctionTool")
    for tool in tool_specs:
        require_async_tool(tool, "wrap_llamaindex", "FunctionTool")

    wrapped_tools = [
        _instrument_index_tool(tool, tiers, compensations, client) for tool in tools
    ]
    wrapped_specs = [
        _instrument_index_tool(tool, tiers, compensations, client)
        for tool in tool_specs
    ]

    if has_tools and index.tools is not None:
        index.tools = wrapped_tools
    if has_specs and index.tool_specs is not None:
        index.tool_specs = wrapped_specs


class WrappedIndex:
    """Index or agent facade whose execution runs inside an UndoLog session.

    The facade adds ``aquery``, ``achat``, and the UndoLog run properties;
    every other attribute resolves on the wrapped index or agent runner.
    """

    def __init__(
        self,
        index: Any,
        org_id: str,
        tiers: dict[str, ToolTier],
        compensations: dict[str, str],
        client: UndoLogClient | None,
        multi_turn: bool = False,
    ) -> None:
        """Bind the facade to an index or agent runner.

        Parameters:
            index: Any object exposing ``aquery`` or ``achat`` alongside
                ``tools`` or ``tool_specs``.
            org_id: Organisation identifier for new sessions.
            tiers: Per-tool tier overrides keyed by tool name.
            compensations: Per-tool compensation registry names keyed
                by tool name.
            client: Optional explicit ``UndoLogClient``.
            multi_turn: When True, multi-turn conversations persist
                session state across queries or chat turns.
        """
        self._index = index
        self._org_id = org_id
        self._tiers = tiers
        self._compensations = compensations
        self._client = client
        self._multi_turn = multi_turn
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
        """Session id of the most recent run, or ``None`` before the first."""
        return self._session_id

    @property
    def step_index(self) -> int:
        """Step progress reached by the most recent run."""
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

        Raises:
            AttributeError: If the attribute is private or the wrapped
                index lacks it.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._index, name)

    async def aquery(self, query: Any, **kwargs: Any) -> Any:
        """Query the index or agent inside an UndoLog session."""
        return await self._run_with_session("aquery", query, is_chat=False, **kwargs)

    async def achat(self, message: Any, **kwargs: Any) -> Any:
        """Chat with the agent inside an UndoLog session across multiple turns."""
        return await self._run_with_session("achat", message, is_chat=True, **kwargs)

    async def _run_with_session(
        self,
        method_name: str,
        arg: Any,
        is_chat: bool,
        **kwargs: Any,
    ) -> Any:
        session_id = kwargs.pop(INPUT_SESSION_KEY, None)
        step_arg = kwargs.pop(INPUT_STEP_KEY, None)

        if session_id is None:
            if (is_chat or self._multi_turn) and self._session_id is not None:
                session_id = self._session_id
                start_step = self._step_index if step_arg is None else int(step_arg)
            else:
                start_step = 0 if step_arg is None else int(step_arg)
        else:
            start_step = 0 if step_arg is None else int(step_arg)

        _prepare_index(self._index, self._tiers, self._compensations, self._client)

        async with UndoLogSession(org_id=self._org_id) as session:
            if session_id:
                session.session_id = str(session_id)
            session._step_index = start_step
            self._session_id = session.session_id
            self._step_index = start_step
            self._awaiting_approval = False
            self._approval_request = None
            try:
                async with run_with_session(session):
                    target_fn = getattr(self._index, method_name)
                    return await target_fn(arg, **kwargs)
            except AwaitingApprovalError as exc:
                self._awaiting_approval = True
                self._approval_request = {
                    "approval_id": exc.approval_id,
                    "tool_name": exc.tool_name,
                    "step_index": exc.step_index,
                }
                log.info(
                    "undolog %s: approval required tool=%s step=%d "
                    "approval_id=%s session=%s",
                    method_name,
                    exc.tool_name,
                    exc.step_index,
                    exc.approval_id,
                    session.session_id,
                )
                raise
            finally:
                self._step_index = session._step_index


def wrap_llamaindex(
    index: Any,
    org_id: str | None = None,
    tiers: dict[str, ToolTier] | None = None,
    compensations: dict[str, str] | None = None,
    client: UndoLogClient | None = None,
    *,
    multi_turn: bool = False,
) -> WrappedIndex:
    """Run a LlamaIndex query or chat agent inside an UndoLog session.

    Parameters:
        index: Anything exposing ``aquery`` or ``achat`` alongside
            ``tools`` or ``tool_specs``.
        org_id: Organisation identifier for sessions. Defaults to the
            ``UNDOLOG_ORG_ID`` environment variable, then ``org_demo``.
        tiers: Per-tool tier overrides keyed by tool name. Tools not
            listed default to heuristics classification.
        compensations: Per-tool compensation registry names keyed by
            tool name. Required for any ``COMPENSABLE`` tool that is
            not already decorated with ``@undolog_tool``.
        client: Optional explicit ``UndoLogClient``.
        multi_turn: When True, multi-turn conversations persist
            session state across queries or chat turns.

    Returns:
        A ``WrappedIndex`` facade.
    """
    resolved_org = org_id or os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    return WrappedIndex(
        index,
        resolved_org,
        tiers or {},
        compensations or {},
        client,
        multi_turn=multi_turn,
    )
