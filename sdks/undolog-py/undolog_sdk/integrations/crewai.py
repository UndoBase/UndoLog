"""CrewAI auto-instrumentation for the UndoLog SDK.

``wrap_crewai`` runs a crew's ``kickoff_async`` inside an
``UndoLogSession``, so tool calls made through the crew's agents reach
the proxy: every call is journaled and eligible for replay or approval.

Design notes:
    *   **Duck-typed, no CrewAI import.** The facade needs
        ``kickoff_async`` and ``agents``; attributes it does not define
        resolve on the crew.
    *   **Only ``kickoff_async`` opens a session and wraps the tools.**
        A synchronous ``kickoff`` gets neither, so a decorated tool
        raises ``RuntimeError`` for the missing session.
    *   **Tools are instrumented at kickoff.** Each agent's tool list
        is reassigned to instrumented copies before the crew runs. The
        wrap is idempotent, so a second kickoff neither re-wraps nor
        journals a call twice, and every agent is prepared before any
        is reassigned, so a rejected tool leaves the crew untouched.
    *   **Approvals propagate.** ``AwaitingApprovalError`` escapes
        ``kickoff_async`` with the run's identity already recorded on
        the facade, which is the flow
        ``docs/guides/integrating-crewai.md`` documents. A callback
        registered for task completion cannot carry it, because the
        task it would report on never completes. The caller resolves
        the approval and re-invokes with ``session_id``, which restarts
        the counter so steps that already completed replay instead of
        running again.
    *   **The session travels through the context var.** Tools called
        inside ``kickoff_async`` resolve it from ``run_with_session``.
        Only async tools are instrumented, because ``undolog_tool``
        awaits its target: a sync tool would fail at its first call, so
        it is rejected at wrap time instead.

Example::

    from undolog_sdk.integrations import wrap_crewai

    crew = wrap_crewai(crew, org_id="org_demo")
    result = await crew.kickoff_async()
"""

from __future__ import annotations

import inspect
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

# wrap_tools is the shared instrumenter: its container rules (a
# callable, or an object exposing ``coroutine``) are the surface both
# wrappers meet, so CrewAI calls it rather than repeating them.
from undolog_sdk.integrations.langgraph import wrap_tools

log = logging.getLogger(__name__)

INPUT_SESSION_KEY = "session_id"
"""``kickoff_async`` input key carrying the stable UndoLog session id."""

INPUT_STEP_KEY = "undolog_step_index"
"""``kickoff_async`` input key carrying the live step counter."""


def _require_async_tool(tool: Any) -> None:
    """Reject a tool that ``undolog_tool`` could not actually intercept.

    Parameters:
        tool: A crew tool: a callable, or an object exposing a
            ``coroutine`` attribute.

    Raises:
        ValueError: If the callable that would run is not a coroutine
            function. ``undolog_tool`` awaits its target, so a sync
            tool would fail at its first call, and the wrap would have
            promised interception that never happens.
    """
    inner = getattr(tool, "coroutine", None)
    target = inner if callable(inner) else tool
    if not callable(target) or inspect.iscoroutinefunction(target):
        return
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool
    raise ValueError(
        f"wrap_crewai cannot instrument {name!r}: UndoLog awaits the wrapped "
        "callable, so the tool must be async. Use an async function, or a "
        "StructuredTool exposing an async 'coroutine'."
    )


def _prepare_agents(
    crew: Any,
    tiers: dict[str, ToolTier],
    compensations: dict[str, str],
    client: UndoLogClient | None,
) -> None:
    """Instrument every tool on every agent of ``crew``, in place.

    Parameters:
        crew: The crew to prepare. Must expose ``agents``.
        tiers: Per-tool tier overrides keyed by tool name.
        compensations: Per-tool compensation registry names keyed by
            tool name.
        client: Optional explicit ``UndoLogClient``.

    Raises:
        AttributeError: If the crew or one of its agents lacks the
            interface the wrapper needs.
        ValueError: If a tool cannot be instrumented. Every agent is
            wrapped before any agent is reassigned, so a failure here
            leaves the crew exactly as it was.
    """
    plans: list[tuple[Any, list[Any]]] = []
    for agent in crew.agents:
        tools = list(agent.tools or [])
        for tool in tools:
            _require_async_tool(tool)
        plans.append(
            (
                agent,
                wrap_tools(
                    tools, tiers=tiers, compensations=compensations, client=client
                ),
            )
        )
    for agent, wrapped in plans:
        agent.tools = wrapped


class WrappedCrew:
    """Crew facade whose ``kickoff_async`` runs inside an UndoLog session.

    The facade adds ``kickoff_async`` and the UndoLog run properties;
    every other attribute resolves on the wrapped crew. ``kickoff_async``
    instruments the agents' tools, creates or resumes an
    ``UndoLogSession``, runs the crew inside ``run_with_session`` so
    wrapped tools resolve the session from the context var, and records
    the run on the facade. Only ``kickoff_async`` opens a session.
    """

    def __init__(
        self,
        crew: Any,
        org_id: str,
        tiers: dict[str, ToolTier],
        compensations: dict[str, str],
        client: UndoLogClient | None,
    ) -> None:
        """Bind the facade to a crew.

        Parameters:
            crew: Any object exposing ``kickoff_async`` and ``agents``.
            org_id: Organisation identifier for new sessions.
            tiers: Per-tool tier overrides keyed by tool name.
            compensations: Per-tool compensation registry names keyed
                by tool name.
            client: Optional explicit ``UndoLogClient``.
        """
        self._crew = crew
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
        """Delegate unknown attributes to the wrapped crew.

        Only called for attributes not found on the facade itself.
        Underscore-prefixed lookups are never delegated, which keeps
        ``self._crew`` access safe before the attribute exists.

        Raises:
            AttributeError: If the attribute is private or the wrapped
                crew lacks it.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._crew, name)

    async def kickoff_async(
        self,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run the crew inside an UndoLog session.

        Pass ``session_id`` in ``inputs`` to resume that journal. On a
        retry, leave ``undolog_step_index`` out: the counter restarts,
        so each call lands on the step it already journaled and the
        engine replays it rather than running it again. Pass
        ``undolog_step_index`` as well to continue past those steps.
        Both keys are removed before the crew sees the inputs, because
        they are UndoLog bookkeeping rather than task placeholders.

        Parameters:
            inputs: Task inputs. May carry ``session_id`` to resume a
                journal and ``undolog_step_index`` to continue its
                counter.
            **kwargs: Extra keyword arguments, forwarded to
                ``kickoff_async``.

        Returns:
            Whatever the crew returns, unchanged.

        Raises:
            ValueError: If any agent exposes a tool that cannot be
                instrumented. The crew is left untouched when that
                happens.
            AwaitingApprovalError: When the proxy suspends an
                irreversible tool. The facade records the run first, so
                ``session_id`` and ``approval_request`` are readable
                from the handler.
        """
        payload = dict(inputs) if inputs else {}
        session_id = payload.pop(INPUT_SESSION_KEY, None)
        start_step = int(payload.pop(INPUT_STEP_KEY, 0) or 0)

        _prepare_agents(self._crew, self._tiers, self._compensations, self._client)

        async with UndoLogSession(org_id=self._org_id) as session:
            if session_id:
                # Resume the journal the previous run wrote to instead of
                # starting a new one.
                session.session_id = str(session_id)
            session._step_index = start_step
            self._session_id = session.session_id
            self._step_index = start_step
            self._awaiting_approval = False
            self._approval_request = None
            try:
                async with run_with_session(session):
                    return await self._crew.kickoff_async(payload or None, **kwargs)
            except AwaitingApprovalError as exc:
                self._awaiting_approval = True
                self._approval_request = {
                    "approval_id": exc.approval_id,
                    "tool_name": exc.tool_name,
                    "step_index": exc.step_index,
                }
                log.info(
                    "undolog kickoff_async: approval required tool=%s step=%d "
                    "approval_id=%s session=%s",
                    exc.tool_name,
                    exc.step_index,
                    exc.approval_id,
                    session.session_id,
                )
                raise
            finally:
                # Report the progress the run actually reached, including
                # the step that suspended it.
                self._step_index = session._step_index


def wrap_crewai(
    crew: Any,
    org_id: str | None = None,
    tiers: dict[str, ToolTier] | None = None,
    compensations: dict[str, str] | None = None,
    client: UndoLogClient | None = None,
) -> WrappedCrew:
    """Run a CrewAI crew inside an UndoLog session, in one call.

    Parameters:
        crew: A ``Crew`` (anything exposing ``kickoff_async`` and
            ``agents``).
        org_id: Organisation identifier for sessions. Defaults to the
            ``UNDOLOG_ORG_ID`` environment variable, then ``org_demo``.
        tiers: Per-tool tier overrides keyed by tool name. Tools not
            listed default to ``COMPENSABLE``.
        compensations: Per-tool compensation registry names keyed by
            tool name. Required for any ``COMPENSABLE`` tool that is
            not already decorated with ``@undolog_tool``.
        client: Optional explicit ``UndoLogClient``.

    Returns:
        A ``WrappedCrew`` facade. The original crew is not replaced;
        its agents' tool lists are reassigned to the instrumented
        copies at kickoff.

    Raises:
        ValueError: At call time, if a tool cannot be instrumented.

    Note:
        Tools already decorated with ``@undolog_tool`` come back
        untouched, so a crew built from decorated tools needs no tier
        or compensation mapping.

    Example:
        Single-line integration::

            crew = wrap_crewai(crew, org_id="org_demo")
            result = await crew.kickoff_async()
    """
    resolved_org = org_id or os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    return WrappedCrew(
        crew,
        resolved_org,
        tiers or {},
        compensations or {},
        client,
    )
