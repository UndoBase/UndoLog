"""LangGraph auto-instrumentation for the UndoLog SDK.

``wrap_langgraph`` runs a compiled graph's ``ainvoke`` inside an
``UndoLogSession``, so tool calls made through ``wrap_tools`` reach
the proxy: every call is journaled and eligible for replay or
approval.

Design notes:
    *   **Duck-typed, no LangGraph import.** The facade needs only
        ``ainvoke``; attributes it does not define resolve on the
        graph.
    *   **Only ``ainvoke`` opens a session.** ``astream`` runs without
        one, so an instrumented non-SAFE tool raises ``RuntimeError``;
        open ``run_with_session`` yourself around a streaming call.
    *   **Tools are wrapped before the graph is built**, because a
        compiled graph already holds its own references to them.
    *   **State carries identity.** ``session_id`` and
        ``undolog_step_index`` are echoed back in the output, so the
        next invocation resumes the same journal, following
        ``examples/langchain-support-agent/agent_stateful.py``.
    *   **Approvals become state.** ``awaiting_approval`` and
        ``approval_request`` replace an escaping
        ``AwaitingApprovalError``, since ``interrupt()`` only works
        from inside a node and would add a framework dependency. The
        caller resolves it through the proxy and re-invokes with the
        returned state: completed steps replay exactly once.

Example::

    from undolog_sdk.integrations import wrap_langgraph

    app = wrap_langgraph(build_graph(), org_id="org_demo")
    result = await app.ainvoke({"customer_query": "hi"}, config=config)
    if result["awaiting_approval"]:
        # Resolve POST /approvals/{approval_id}/approve, then re-invoke.
        ...
"""

from __future__ import annotations

import copy
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from undolog_sdk import (
    AwaitingApprovalError,
    CompensationDescriptor,
    ToolTier,
    UndoLogClient,
    UndoLogSession,
    undolog_tool,
)
from undolog_sdk.context import run_with_session

log = logging.getLogger(__name__)

STATE_SESSION_KEY = "session_id"
"""Graph-state key carrying the stable UndoLog session id."""

STATE_STEP_KEY = "undolog_step_index"
"""Graph-state key carrying the live step counter."""

STATE_AWAITING_KEY = "awaiting_approval"
"""Graph-state key flagging that a tool is waiting for approval."""

STATE_APPROVAL_KEY = "approval_request"
"""Graph-state key carrying the pending approval details."""


def wrap_tool(
    tool: Callable[..., Awaitable[Any]],
    tier: ToolTier = ToolTier.COMPENSABLE,
    compensation_name: str | None = None,
    client: UndoLogClient | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap one async tool with the ``@undolog_tool`` interceptor.

    Args:
        tool: The async callable to instrument.
        tier: UndoLog tier for the tool. Defaults to ``COMPENSABLE``.
        compensation_name: Name of the compensation registered in the
            engine registry. Required when ``tier`` is ``COMPENSABLE``.
        client: Optional explicit ``UndoLogClient``.

    Returns:
        The instrumented tool, or ``tool`` unchanged when it already
        carries UndoLog instrumentation: a second wrap would journal
        every call twice, and the tier chosen at decoration time
        stands.

    Raises:
        ValueError: If ``tier`` is ``COMPENSABLE`` and no
            ``compensation_name`` was given, which would leave the
            tool running with no journal, no compensation, and no
            approval gate.
        AwaitingApprovalError: At call time, when the proxy suspends
            the tool for human approval. ``wrap_tool`` does not catch
            it: the integration entry point records the run and
            reports the pending approval to its caller.
    """
    if getattr(tool, "_undolog_tool_name", None) is not None:
        return tool
    if tier is ToolTier.COMPENSABLE and not compensation_name:
        raise ValueError(
            f"Compensable tool {getattr(tool, '__name__', tool)!r} has no "
            "compensation name. Pass compensation_name=..., or classify the "
            "tool with tier=ToolTier.SAFE or ToolTier.IRREVERSIBLE."
        )

    descriptor = (
        CompensationDescriptor.new(compensation_name) if compensation_name else None
    )
    return undolog_tool(tier, compensation=descriptor, client=client)(tool)


def wrap_tools(
    tools: Sequence[Any],
    tiers: dict[str, ToolTier] | None = None,
    compensations: dict[str, str] | None = None,
    client: UndoLogClient | None = None,
) -> list[Any]:
    """Wrap a tool list with UndoLog instrumentation.

    Pass the returned list to the framework when you assemble it: a
    tool the framework already holds a reference to cannot be
    instrumented afterwards.

    Args:
        tools: Tool objects. Plain async callables are
            wrapped directly; objects exposing an async ``coroutine``
            attribute (pydantic ``StructuredTool``-style) have that
            attribute swapped for the wrapped version on a shallow
            copy of the container.
        tiers: Per-tool tier overrides keyed by tool name. Tools not
            listed default to ``COMPENSABLE``.
        compensations: Per-tool compensation registry names keyed by
            tool name. Required for any ``COMPENSABLE`` tool.
        client: Optional explicit ``UndoLogClient``.

    Returns:
        A new list of tools. Structured-tool containers are shallow-
        copied before their callable attribute is replaced, so the
        caller's original tool objects are never mutated. Tools that
        already carry UndoLog instrumentation come back untouched,
        keeping the tier chosen when they were decorated.

    Raises:
        ValueError: If a ``COMPENSABLE`` tool has no entry in
            ``compensations``, or if a tool is not a callable that
            carries a name. Tools default to ``COMPENSABLE``, so an
            unmapped tool is rejected instead of running with no
            journal and no approval gate.
    """
    tiers = tiers or {}
    compensations = compensations or {}
    wrapped: list[Any] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        target = tool
        container: Any = None
        inner = getattr(tool, "coroutine", None)
        if inner is not None and callable(inner):
            target = inner
            container = tool
        if not callable(target) or not name or not hasattr(target, "__name__"):
            raise ValueError(
                f"wrap_tools cannot instrument {name or tool!r}: UndoLog "
                "needs an async callable with a name, or an object exposing "
                "an async 'coroutine' attribute."
            )
        if getattr(target, "_undolog_tool_name", None) is not None:
            # Already instrumented: keep the caller's object as it is.
            wrapped.append(tool)
            continue
        tier = tiers.get(name, ToolTier.COMPENSABLE)
        instrumented = wrap_tool(
            target,
            tier=tier,
            compensation_name=compensations.get(name),
            client=client,
        )
        if container is not None:
            replica = copy.copy(tool)
            setattr(replica, "coroutine", instrumented)
            wrapped.append(replica)
        else:
            wrapped.append(instrumented)
    return wrapped


class WrappedGraph:
    """Graph facade whose ``ainvoke`` runs inside an UndoLog session.

    The facade adds ``ainvoke`` and an ``org_id`` property; every
    other attribute resolves on the wrapped graph. ``ainvoke``
    creates or resumes an ``UndoLogSession``, runs the graph inside
    ``run_with_session`` so wrapped tools resolve the session from
    the context var, and mirrors session identity and step progress
    into the output state. Only ``ainvoke`` opens a session:
    delegated async entry points such as ``astream`` run without
    one, so an instrumented non-SAFE tool raises ``RuntimeError``
    there unless the caller opened ``run_with_session``.
    """

    def __init__(self, graph: Any, org_id: str) -> None:
        """Bind the facade to a graph.

        Args:
            graph: Any object exposing ``ainvoke`` (a compiled
                LangGraph app).
            org_id: Organisation identifier for new sessions.
        """
        self._graph = graph
        self._org_id = org_id

    @property
    def org_id(self) -> str:
        """Organisation identifier used for new sessions."""
        return self._org_id

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the wrapped graph.

        Only called for attributes not found on the facade itself.
        Underscore-prefixed lookups are never delegated, which keeps
        ``self._graph`` access safe before the attribute exists (for
        example during unpickling).

        Raises:
            AttributeError: If the attribute is private or the wrapped
                graph lacks it.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._graph, name)

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke the graph inside an UndoLog session.

        A ``session_id`` in the input resumes that session across
        checkpoint restores; otherwise a fresh session is created.
        The four UndoLog keys in the return value belong to this
        wrapper: it overwrites them in every returned dict.

        Args:
            input: Graph input state. May carry ``session_id`` and
                ``undolog_step_index`` from a previous invocation.
            config: LangGraph runnable config, forwarded unchanged.
            **kwargs: Extra keyword arguments, forwarded to ``ainvoke``.

        Returns:
            The graph output state, augmented with ``session_id``,
            ``undolog_step_index``, ``awaiting_approval``, and
            ``approval_request``. When approval is pending, the
            returned state also repeats the input, so the caller can
            re-invoke with it unchanged once the approval resolves.
            Output that is not a dict is returned as it is, without
            the UndoLog keys.
        """
        session_id = input.get(STATE_SESSION_KEY)
        start_step = int(input.get(STATE_STEP_KEY, 0) or 0)
        async with UndoLogSession(org_id=self._org_id) as session:
            if session_id:
                # Resume the same journal across checkpoint restores.
                session.session_id = str(session_id)
            # Carry the step progress echoed by the previous invocation.
            session._step_index = start_step
            try:
                async with run_with_session(session):
                    result = await self._graph.ainvoke(input, config=config, **kwargs)
            except AwaitingApprovalError as exc:
                log.info(
                    "undolog ainvoke: approval required tool=%s step=%d "
                    "approval_id=%s session=%s",
                    exc.tool_name,
                    exc.step_index,
                    exc.approval_id,
                    session.session_id,
                )
                return {
                    **input,
                    STATE_SESSION_KEY: session.session_id,
                    STATE_STEP_KEY: session._step_index,
                    STATE_AWAITING_KEY: True,
                    STATE_APPROVAL_KEY: {
                        "approval_id": exc.approval_id,
                        "tool_name": exc.tool_name,
                        "step_index": exc.step_index,
                    },
                }
            if not isinstance(result, dict):
                log.debug(
                    "undolog ainvoke: non-dict graph output %r; "
                    "session metadata not attached",
                    result,
                )
                return result
            enriched = dict(result)
            enriched[STATE_SESSION_KEY] = session.session_id
            enriched[STATE_STEP_KEY] = session._step_index
            enriched[STATE_AWAITING_KEY] = False
            enriched[STATE_APPROVAL_KEY] = None
        return enriched


def wrap_langgraph(
    graph: Any,
    org_id: str | None = None,
) -> WrappedGraph:
    """Run a LangGraph app inside an UndoLog session, in one call.

    Args:
        graph: A compiled LangGraph app (anything exposing
            ``ainvoke``).
        org_id: Organisation identifier for sessions. Defaults to the
            ``UNDOLOG_ORG_ID`` environment variable, then ``org_demo``.

    Returns:
        A ``WrappedGraph`` facade; the original graph is not mutated.

    Note:
        Instrument the tools first, with ``wrap_tools``, and build the
        graph from the list it returns. A compiled graph already holds
        its own references to the callables, so this wrapper cannot
        instrument them afterwards.

    Example:
        Single-line integration::

            app = wrap_langgraph(build_graph(), org_id="org_demo")
    """
    resolved_org = org_id or os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    return WrappedGraph(graph, resolved_org)
