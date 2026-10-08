"""Semantic Kernel auto-instrumentation for the UndoLog SDK.

``wrap_semantic_kernel`` registers plugin functions that reach the proxy
and runs each invocation inside an ``UndoLogSession``, so every call is
journaled and eligible for replay or approval.

Design notes:
    *   **Duck-typed, no Semantic Kernel import.** The facade needs
        ``add_function``, ``invoke``, and ``invoke_prompt``; attributes
        it does not define resolve on the kernel.
    *   **Functions are instrumented at registration.** A registered
        plugin function keeps its own reference to the callable it
        wraps, so the same reasoning as ``wrap_langgraph`` applies: the
        wrap happens before the kernel holds the function rather than
        after. ``add_function`` and ``add_functions`` instrument before
        delegating, and both are idempotent, so a function already
        decorated with ``@undolog_tool`` is registered unchanged, with
        the tier chosen at decoration time.
    *   **Registration is all-or-nothing.** ``add_functions`` accepts
        every function in the mapping before registering any, so one
        rejected function leaves the kernel exactly as it was.
    *   **Registration forwards by keyword.** ``add_function`` is called
        with ``plugin_name``, ``function_name``, and ``func`` as keyword
        arguments. A kernel version that names them differently raises
        ``TypeError`` at registration, before it holds the function,
        rather than at the first call.
    *   **Approvals propagate.** ``AwaitingApprovalError`` escapes the
        invocation with the run's identity already recorded on the
        facade, which is the flow ``docs/guides/integrating-
        semantic-kernel.md`` documents. The caller resolves the approval
        and invokes again with ``session_id``, which restarts the
        counter so steps that already completed replay instead of
        running again.
    *   **The session travels through the context var.** Functions
        called inside the invocation resolve it from
        ``run_with_session``. Only async functions are instrumented,
        because ``undolog_tool`` awaits its target: a sync function
        would fail at its first call, so it is rejected at wrap time
        instead.
    *   **Entry points the facade does not wrap get ``session()``.**
        ``invoke`` and ``invoke_prompt`` open a session per call, but an
        agent invokes the kernel on its own schedule. ``session()`` is
        the context manager to run such a call in, so the functions it
        reaches resolve the same session. It takes the same
        ``session_id`` and ``start_step`` as ``invoke``, so the retry
        after an approval replays against the journal it wrote.

Example::

    from undolog_sdk.integrations import wrap_semantic_kernel

    kernel = wrap_semantic_kernel(kernel, org_id="org_demo")
    kernel.add_function("support", "lookup_customer", lookup_customer)
    result = await kernel.invoke_prompt(
        function_name="chat", plugin_name="support", prompt=prompt
    )
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

from undolog_sdk import (
    AwaitingApprovalError,
    ToolTier,
    UndoLogClient,
    UndoLogSession,
)
from undolog_sdk.context import run_with_session
from undolog_sdk.integrations._instrument import require_async_tool

# wrap_tool is the shared instrumenter: its tier, compensation, and
# already-decorated rules are the surface every wrapper meets, so this
# module calls it rather than repeating them.
from undolog_sdk.integrations.langgraph import wrap_tool

log = logging.getLogger(__name__)

INPUT_SESSION_KEY = "session_id"
"""Invocation argument key carrying the stable UndoLog session id."""

INPUT_STEP_KEY = "undolog_step_index"
"""Invocation argument key carrying the live step counter."""


def _require_instrumentable(function: Any) -> None:
    """Reject a plugin function the SDK could not actually intercept.

    The callable and name rules are specific to this one: the kernel
    registers a callable it can invoke and name, and a nameless callable
    would be recorded in the journal as something the caller cannot
    recognise. That rules out a container exposing its callable as
    ``coroutine``, the shape CrewAI's StructuredTool has, which this
    wrapper rejects rather than unwrapping on the caller's behalf.

    The async rule is shared with the CrewAI wrapper, and applies here
    once the callable and name have been accepted.

    Parameters:
        function: A callable intended for registration with the kernel.

    Raises:
        ValueError: If the function is not a callable carrying a name, or
            if it is not a coroutine function. ``undolog_tool`` awaits its
            target, so a sync function would fail at its first call, and
            the wrap would have promised interception that never happens.
    """
    name = getattr(function, "__name__", None)
    if not callable(function) or not name:
        raise ValueError(
            f"wrap_semantic_kernel cannot instrument {function!r}: UndoLog "
            "needs an async callable with a name, because it awaits the "
            "function it wraps."
        )
    # Containers are rejected above, so no container hint applies here.
    require_async_tool(function, "wrap_semantic_kernel", None)


def _pop_resume_keys(arguments: Any) -> tuple[Any, int]:
    """Read and remove the UndoLog resume keys from an arguments mapping.

    Parameters:
        arguments: The mapping the caller passed to the invocation, or
            ``None`` when it passed none.

    Returns:
        The ``(session_id, start_step)`` pair. Both default, so a call
        with no resume keys opens a new session at step zero.

    Raises:
        TypeError: If a resume key is present and the mapping cannot be
            mutated. The keys have to disappear before Semantic Kernel
            reads the arguments, so a mapping that cannot give them up
            is refused rather than left to leak bookkeeping into a
            function argument.
    """
    if arguments is None:
        return None, 0
    has_keys = INPUT_SESSION_KEY in arguments or INPUT_STEP_KEY in arguments
    if has_keys and not isinstance(arguments, MutableMapping):
        raise TypeError(
            "wrap_semantic_kernel needs a mutable arguments mapping to consume "
            f"the resume keys, got {type(arguments).__name__}"
        )
    session_id = arguments.pop(INPUT_SESSION_KEY, None) if has_keys else None
    start_step = int(arguments.pop(INPUT_STEP_KEY, 0) or 0) if has_keys else 0
    return session_id, start_step


class WrappedKernel:
    """Kernel facade whose invocations run inside an UndoLog session.

    The facade adds ``add_function``, ``add_functions``, ``invoke``,
    ``invoke_prompt``, ``session``, and the UndoLog run properties;
    every other attribute resolves on the wrapped kernel. An invocation
    instruments nothing, because the functions were instrumented when
    they were registered: the invocation creates or resumes an
    ``UndoLogSession``, runs inside ``run_with_session`` so registered
    functions resolve the session from the context var, and records the
    run on the facade. ``invoke`` and ``invoke_prompt`` open a session
    per call; ``session`` opens one for an entry point the facade does
    not wrap, such as an agent run.
    """

    def __init__(
        self,
        kernel: Any,
        org_id: str,
        tiers: dict[str, ToolTier],
        compensations: dict[str, str],
        client: UndoLogClient | None,
    ) -> None:
        """Bind the facade to a kernel.

        Parameters:
            kernel: Any object exposing ``add_function``, ``invoke``,
                and ``invoke_prompt``.
            org_id: Organisation identifier for new sessions.
            tiers: Per-function tier overrides keyed by function name.
            compensations: Per-function compensation registry names
                keyed by function name.
            client: Optional explicit ``UndoLogClient``.
        """
        self._kernel = kernel
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
        """Delegate unknown attributes to the wrapped kernel.

        Only called for attributes not found on the facade itself.
        Underscore-prefixed lookups are never delegated, which keeps
        ``self._kernel`` access safe before the attribute exists.

        Raises:
            AttributeError: If the attribute is private or the wrapped
                kernel lacks it.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._kernel, name)

    def add_function(
        self,
        plugin_name: str,
        function_name: str,
        func: Any,
        **kwargs: Any,
    ) -> Any:
        """Instrument one plugin function and register it with the kernel.

        Parameters:
            plugin_name: Plugin to register the function under.
            function_name: Name the kernel exposes it as.
            func: The async callable to instrument. A function already
                decorated with ``@undolog_tool`` is registered as it is,
                keeping the tier chosen at decoration time.
            **kwargs: Extra keyword arguments, forwarded to the
                kernel's ``add_function``.

        Returns:
            Whatever the kernel's ``add_function`` returns.

        Raises:
            ValueError: If the function cannot be instrumented, because
                it is not an async callable with a name, or because it
                is ``COMPENSABLE`` with no compensation name.
        """
        instrumented = self._instrument(function_name, func)
        return self._kernel.add_function(
            plugin_name=plugin_name,
            function_name=function_name,
            func=instrumented,
            **kwargs,
        )

    def add_functions(
        self,
        plugin_name: str,
        functions: Mapping[str, Any],
        **kwargs: Any,
    ) -> None:
        """Instrument every function of a plugin and register them.

        Every function is instrumented before any is registered, so one
        rejected function leaves the kernel exactly as it was.

        Parameters:
            plugin_name: Plugin to register the functions under.
            functions: Functions keyed by the name the kernel exposes
                them as.
            **kwargs: Extra keyword arguments, forwarded to each call of
                the kernel's ``add_function``.

        Raises:
            ValueError: If any function cannot be instrumented. Nothing
                is registered when that happens.
        """
        registered = [
            (name, self._instrument(name, function))
            for name, function in functions.items()
        ]
        for name, instrumented in registered:
            self._kernel.add_function(
                plugin_name=plugin_name,
                function_name=name,
                func=instrumented,
                **kwargs,
            )

    def _instrument(self, name: str, function: Any) -> Any:
        """Return ``function`` carrying UndoLog instrumentation.

        Parameters:
            name: Function name, used to look up its tier and
                compensation overrides.
            function: The callable to instrument.

        Returns:
            The instrumented function, or ``function`` unchanged when it
            is already decorated.

        Raises:
            ValueError: If the function cannot be instrumented.
        """
        _require_instrumentable(function)
        return wrap_tool(
            function,
            tier=self._tiers.get(name, ToolTier.COMPENSABLE),
            compensation_name=self._compensations.get(name),
            client=self._client,
        )

    async def invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Run a plugin function inside an UndoLog session.

        Pass ``session_id`` in the arguments to resume that journal. On
        a retry, leave ``undolog_step_index`` out: the counter
        restarts, so each call lands on the step it already journaled
        and the engine replays it rather than running it again. Pass
        ``undolog_step_index`` as well to continue past those steps
        instead. Both keys are removed before the kernel reads the
        arguments, because they are UndoLog bookkeeping rather than
        function arguments.

        Parameters:
            *args: Positional arguments forwarded to ``invoke``. The
                second one, when it is a mapping, is the arguments the
                resume keys are taken from.
            **kwargs: Keyword arguments forwarded to ``invoke``.
                ``arguments`` is the mapping the resume keys are taken
                from.

        Returns:
            Whatever the kernel returns, unchanged.

        Raises:
            TypeError: If the arguments carry a resume key and cannot be
                mutated.
            AwaitingApprovalError: When the proxy suspends an
                irreversible function. The facade records the run first,
                so ``session_id`` and ``approval_request`` are readable
                from the handler.
        """
        arguments = kwargs.get("arguments")
        if arguments is None and len(args) > 1:
            arguments = args[1]
        session_id, start_step = _pop_resume_keys(arguments)
        return await self._run_in_session(
            session_id, start_step, lambda: self._kernel.invoke(*args, **kwargs)
        )

    async def invoke_prompt(self, *args: Any, **kwargs: Any) -> Any:
        """Run a prompt function inside an UndoLog session.

        The same session, resume-key, and approval behaviour as
        ``invoke``; read its docstring for the retry recipe. Resume keys
        go in ``arguments``.

        Parameters:
            *args: Positional arguments forwarded to ``invoke_prompt``.
            **kwargs: Keyword arguments forwarded to ``invoke_prompt``.

        Returns:
            Whatever the kernel returns, unchanged.

        Raises:
            TypeError: If the arguments carry a resume key and cannot be
                mutated.
            AwaitingApprovalError: When the proxy suspends an
                irreversible function, after the run has been recorded.
        """
        session_id, start_step = _pop_resume_keys(kwargs.get("arguments"))
        return await self._run_in_session(
            session_id,
            start_step,
            lambda: self._kernel.invoke_prompt(*args, **kwargs),
        )

    def _reset_run(self, session: UndoLogSession, start_step: int) -> None:
        """Point the facade's run properties at a new session.

        Parameters:
            session: The session the next block of work will run in.
            start_step: Step the counter starts from.
        """
        self._session_id = session.session_id
        self._step_index = start_step
        self._awaiting_approval = False
        self._approval_request = None

    def _record_approval(
        self, exc: AwaitingApprovalError, session: UndoLogSession
    ) -> None:
        """Record a pending approval on the facade before it propagates.

        Parameters:
            exc: The approval the proxy is waiting on.
            session: The session the suspended run was writing to.
        """
        self._awaiting_approval = True
        self._approval_request = {
            "approval_id": exc.approval_id,
            "tool_name": exc.tool_name,
            "step_index": exc.step_index,
        }
        log.info(
            "undolog approval required tool=%s step=%d approval_id=%s session=%s",
            exc.tool_name,
            exc.step_index,
            exc.approval_id,
            session.session_id,
        )

    @asynccontextmanager
    async def _open_session(
        self,
        session_id: Any,
        start_step: int,
    ) -> AsyncIterator[UndoLogSession]:
        """Open a session, optionally resuming a journal, and publish it.

        Parameters:
            session_id: Journal to resume, or ``None`` for a new one.
            start_step: Step the counter starts from.

        Yields:
            The open session, published to the context variable the
            registered functions read.

        Raises:
            AwaitingApprovalError: When the proxy suspends an irreversible
                function inside the block, after the run has been recorded.
        """
        async with UndoLogSession(org_id=self._org_id) as session:
            if session_id:
                # Resume the journal the previous run wrote to instead
                # of starting a new one. The counter starts at zero, so
                # the retried calls land on the steps they journaled and
                # the engine replays them.
                session.session_id = str(session_id)
            session._step_index = start_step
            self._reset_run(session, start_step)
            try:
                async with run_with_session(session):
                    yield session
            except AwaitingApprovalError as exc:
                self._record_approval(exc, session)
                raise
            finally:
                # Report the progress the run actually reached,
                # including the step that suspended it.
                self._step_index = session._step_index

    @asynccontextmanager
    async def session(
        self,
        session_id: Any = None,
        start_step: int = 0,
    ) -> AsyncIterator[UndoLogSession]:
        """Open a session for work the facade does not wrap itself.

        ``invoke`` and ``invoke_prompt`` open a session per call, so a
        plugin function registered through the facade needs nothing from
        the caller. An agent, however, invokes the kernel on its own
        schedule: a ``ChatCompletionAgent`` run, or any other entry point
        that does not go through this facade. Running that entry point
        inside this context manager publishes the same session to the
        context variable the functions read, so they resolve it instead of
        raising for the missing session.

        The four UndoLog run properties report this session while the
        block is open and the progress it reached once it closes. An
        ``AwaitingApprovalError`` raised inside the block is recorded on
        the facade before it propagates, as it is for ``invoke``.

        Parameters:
            session_id: Journal to resume, or ``None`` for a new one. Pass
                ``kernel.session_id`` after an approval to retry the run
                against the journal it already wrote: the counter starts
                again at zero, so the calls that completed replay instead
                of running twice.
            start_step: Step the counter starts from. Leave it at zero on
                a retry, and pass ``kernel.step_index`` only to continue
                past the steps already journaled and start new work.

        Yields:
            The open ``UndoLogSession``, for the caller that wants to read
            its id or pass it to a kernel call directly.

        Raises:
            AwaitingApprovalError: When the proxy suspends an irreversible
                function inside the block, after the run has been recorded.
        """
        async with self._open_session(session_id, start_step) as session:
            yield session

    async def _run_in_session(
        self,
        session_id: Any,
        start_step: int,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one kernel call inside a session and record the run.

        Parameters:
            session_id: Journal to resume, or ``None`` for a new one.
            start_step: Step the counter starts from.
            call: The kernel call to run inside the session.

        Returns:
            Whatever ``call`` returns.

        Raises:
            AwaitingApprovalError: When the proxy suspends an
                irreversible function, after the run has been recorded
                on the facade.
        """
        async with self._open_session(session_id, start_step):
            return await call()


def wrap_semantic_kernel(
    kernel: Any,
    org_id: str | None = None,
    tiers: dict[str, ToolTier] | None = None,
    compensations: dict[str, str] | None = None,
    client: UndoLogClient | None = None,
) -> WrappedKernel:
    """Register UndoLog plugin functions and run a kernel inside a session.

    Parameters:
        kernel: A ``Kernel`` (anything exposing ``add_function``,
            ``invoke``, and ``invoke_prompt``).
        org_id: Organisation identifier for sessions. Defaults to the
            ``UNDOLOG_ORG_ID`` environment variable, then ``org_demo``.
        tiers: Per-function tier overrides keyed by function name.
            Functions not listed default to ``COMPENSABLE``.
        compensations: Per-function compensation registry names keyed by
            function name. Required for any ``COMPENSABLE`` function
            that is not already decorated with ``@undolog_tool``.
        client: Optional explicit ``UndoLogClient``.

    Returns:
        A ``WrappedKernel`` facade. The original kernel is not replaced;
        functions registered through the facade are instrumented on
        their way in, and every other attribute resolves on the kernel.

    Raises:
        ValueError: At registration time, if a function cannot be
            instrumented.

    Note:
        Register functions through the facade rather than the kernel:
        a function the kernel already holds cannot be instrumented
        afterwards, because it keeps its own reference to the callable
        it wraps. Functions already decorated with ``@undolog_tool``
        need no tier or compensation mapping.

    Example:
        Single-line integration::

            kernel = wrap_semantic_kernel(kernel, org_id="org_demo")
            result = await kernel.invoke(function, arguments={})
    """
    resolved_org = org_id or os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    return WrappedKernel(
        kernel,
        resolved_org,
        tiers or {},
        compensations or {},
        client,
    )
