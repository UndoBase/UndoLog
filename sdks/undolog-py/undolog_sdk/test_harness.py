"""Compensation test harness for isolated unit testing.

Provides ``CompensationTestHarness`` for testing compensation functions
without a running engine or database. Tracks execution counts, arguments,
return values, and supports retry and idempotency verification.

Usage::

    from undolog_sdk.test_harness import CompensationTestHarness

    async def undo_send_email(to: str, subject: str) -> dict:
        return {"status": "undone", "to": to}

    async def test_undo():
        harness = CompensationTestHarness(undo_send_email)
        result = await harness.execute("alice@example.com", subject="Hi")
        assert result == {"status": "undone", "to": "alice@example.com"}
        assert harness.call_count == 1
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_SESSION_RESULT = "compensated"
_HALTED_SESSION_RESULT = "halted"


class NetworkDropoutError(Exception):
    """Simulated network connection failure or timeout during compensation."""


class RateLimitError(Exception):
    """Simulated upstream rate limit (HTTP 429) during compensation.

    Attributes:
        retry_after_ms: Recommended retry wait duration in milliseconds.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded (HTTP 429)",
        *,
        retry_after_ms: int = 1000,
    ) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


def calculate_backoff(
    attempt: int,
    base_backoff_ms: int = 100,
    multiplier: float = 2.0,
    max_backoff_ms: int = 5000,
) -> int:
    """Calculate exponential backoff duration capped at max_backoff_ms.

    Args:
        attempt: Zero-based retry attempt number (0 for the first retry).
        base_backoff_ms: Initial backoff delay in milliseconds.
        multiplier: Factor by which backoff increases each retry.
        max_backoff_ms: Maximum backoff delay in milliseconds.

    Returns:
        Computed delay in milliseconds, guaranteed not to exceed max_backoff_ms.
    """
    if base_backoff_ms <= 0 or attempt < 0:
        return 0
    calculated = int(base_backoff_ms * (multiplier**attempt))
    return min(calculated, max_backoff_ms)


@dataclass
class CompensationTestHarness:
    """Test harness for compensation functions without a running engine.

    Wraps an async compensation function and records every invocation,
    allowing tests to verify execution counts, arguments, return values,
    retry behaviour, and idempotency guarantees.

    Attributes:
        fn: The async compensation function under test.
        max_retries: Maximum retry attempts before giving up.
        call_count: Total number of successful invocations.
        calls: List of ``(args, kwargs)`` tuples for each invocation.
        results: List of return values for each invocation.
        errors: List of ``(call_index, exception)`` for failed invocations.

    Example::

        async def undo_transfer(tx_id: str) -> dict:
            return {"undone": tx_id}

        harness = CompensationTestHarness(undo_transfer)
        result = await harness.execute("tx-123")
        assert result == {"undone": "tx-123"}
        assert harness.call_count == 1
    """

    fn: Callable[..., Coroutine[Any, Any, Any]]
    """The async compensation function to test."""

    max_retries: int = 3
    """Maximum retry attempts before escalating."""

    call_count: int = field(default=0, init=False, repr=False)
    """Total number of successful invocations."""

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list, init=False, repr=False
    )
    """ ``(args, kwargs)`` for each invocation."""

    results: list[Any] = field(default_factory=list, init=False, repr=False)
    """Return value for each invocation."""

    errors: list[tuple[int, BaseException]] = field(
        default_factory=list, init=False, repr=False
    )
    """ ``(call_index, exception)`` for each failed invocation."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the compensation function and record the call.

        Args:
            *args: Positional arguments forwarded to the function.
            **kwargs: Keyword arguments forwarded to the function.

        Returns:
            The return value of the compensation function.

        Raises:
            Exception: Re-raises any exception from the compensation
                function after recording it in ``self.errors``.
        """
        call_index = len(self.calls)
        self.calls.append((args, kwargs))

        try:
            result = await self.fn(*args, **kwargs)
        except Exception as exc:
            self.errors.append((call_index, exc))
            raise

        self.call_count += 1
        self.results.append(result)
        log.debug(
            "execute[%d] %s returned %r",
            call_index,
            getattr(self.fn, "__name__", "?"),
            result,
        )
        return result

    async def execute_with_retry(self, *args: Any, **kwargs: Any) -> Any:
        """Execute with retry logic, re-raising after ``max_retries``.

        Attempts up to ``max_retries`` times. Returns on the first
        successful call. Raises the last exception if all attempts fail.

        Unlike the engine's compensation runner, every exception is
        treated as retryable and no backoff is applied; this is a
        deliberate simplification for in-process testing (see ADR 0017).

        Args:
            *args: Positional arguments forwarded to the function.
            **kwargs: Keyword arguments forwarded to the function.

        Returns:
            The return value of the compensation function.

        Raises:
            Exception: The last exception if all retries are exhausted.
        """
        last_exc: BaseException | None = None
        for attempt in range(self.max_retries):
            try:
                return await self.execute(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                log.debug("execute_with_retry attempt %d failed: %s", attempt, exc)
        assert last_exc is not None
        raise last_exc

    def assert_called_once(self) -> None:
        """Assert the function was called exactly once.

        Raises:
            AssertionError: If ``call_count`` is not 1.
        """
        assert self.call_count == 1, f"expected 1 call, got {self.call_count}"

    def assert_called_with(self, *args: Any, **kwargs: Any) -> None:
        """Assert the function was called with specific arguments.

        Checks the most recent invocation.

        Args:
            *args: Expected positional arguments.
            **kwargs: Expected keyword arguments.

        Raises:
            AssertionError: If the most recent call does not match.
        """
        assert self.calls, "function was never called"
        actual_args, actual_kwargs = self.calls[-1]
        assert actual_args == args, (
            f"positional args mismatch: expected {args}, got {actual_args}"
        )
        assert actual_kwargs == kwargs, (
            f"keyword args mismatch: expected {kwargs}, got {actual_kwargs}"
        )

    async def assert_idempotent(self, *args: Any, **kwargs: Any) -> None:
        """Execute twice with the same arguments and verify identical results.

        Args:
            *args: Arguments to pass to both invocations.
            **kwargs: Keyword arguments to pass to both invocations.

        Raises:
            AssertionError: If the two return values are not equal.
        """
        r1 = await self.execute(*args, **kwargs)
        r2 = await self.execute(*args, **kwargs)
        assert r1 == r2, f"idempotency violation: first={r1!r}, second={r2!r}"

    def reset(self) -> None:
        """Clear all recorded state.

        After calling ``reset()``, the harness behaves as if freshly
        constructed (same ``fn`` and ``max_retries``).
        """
        self.call_count = 0
        self.calls.clear()
        self.results.clear()
        self.errors.clear()
        log.debug("harness reset for %s", getattr(self.fn, "__name__", "?"))


@dataclass(frozen=True)
class EffectDescriptor:
    """One recorded effect in a simulated session rollback.

    Mirrors the fields of the engine's undo stack entry that the
    compensation pass consumes (``UndoEntry`` in the saga crate): the
    tool that ran, its stack position, and the compensation to invoke.

    Attributes:
        tool_name: Logical name of the tool that produced the effect.
        compensation: The async compensation callable to invoke.
        stack_position: LIFO position; higher values roll back first.
        max_retries: Retry budget for this entry. ``0`` selects the
            default budget (3), matching the engine contract.
        args: Positional arguments for the compensation call.
        kwargs: Keyword arguments for the compensation call.
    """

    tool_name: str
    compensation: Callable[..., Coroutine[Any, Any, Any]]
    stack_position: int
    max_retries: int = 0
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate stack position and normalize kwargs to a dict.

        Raises:
            ValueError: If ``stack_position`` is negative.
        """
        if self.stack_position < 0:
            raise ValueError(
                f"stack_position must be non-negative, got {self.stack_position}"
            )
        if self.kwargs is None:
            object.__setattr__(self, "kwargs", {})


@dataclass(frozen=True)
class StepResult:
    """Outcome of one entry in a simulated rollback.

    Attributes:
        tool_name: Logical name of the compensated tool.
        stack_position: LIFO position the entry rolled back at.
        attempts: Number of times the compensation was invoked.
        state: ``"compensated"`` or ``"failed"``.
        error: The last exception message when ``state == "failed"``.
        args: Arguments delivered to the compensation function.
        dead_letter: True if retries were exhausted without success.
    """

    tool_name: str
    stack_position: int
    attempts: int
    state: str
    error: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    dead_letter: bool = False


@dataclass(frozen=True)
class SagaReport:
    """Report of a full simulated rollback.

    Attributes:
        session_result: ``"compensated"`` when every non-terminal entry
            compensated, ``"halted"`` when one failed permanently.
        steps: Per-entry outcomes in rollback (LIFO) execution order.
        compensation_order: Tool names in the order they were invoked.
        dead_letters: Step results that exhausted retries without success.
    """

    session_result: str
    steps: tuple[StepResult, ...]
    compensation_order: tuple[str, ...]
    dead_letters: tuple[StepResult, ...] = ()


@dataclass(frozen=True)
class CompensationReport:
    """Report of a single simulated compensation run.

    Attributes:
        fn_name: Name of the compensation function under test.
        execution_count: Number of invocations that returned normally.
        retry_count: Attempts consumed beyond the initial call.
        final_state: ``"compensated"`` on success, ``"failed"`` when
            every attempt raised.
        result: The return value of the successful attempt.
        error: The last exception message when ``final_state == "failed"``.
        dead_letter: True if retries were exhausted without success.
        backoff_intervals_ms: Backoff intervals calculated for each retry.
    """

    fn_name: str
    execution_count: int
    retry_count: int
    final_state: str
    result: Any = None
    error: str | None = None
    dead_letter: bool = False
    backoff_intervals_ms: tuple[int, ...] = ()


class SagaTestHarness:
    """Simulate the saga orchestrator's compensation pass in-process.

    Accepts recorded effect descriptors and replays the rollback the
    engine would perform: entries execute in LIFO order by
    ``stack_position``, each gets a per-entry retry budget (``0`` means
    the default budget of 3), and the pass stops at the first entry
    that fails permanently, leaving the remaining entries untouched.

    Example::

        harness = SagaTestHarness([
            EffectDescriptor("charge", undo_charge, stack_position=1),
            EffectDescriptor("email", undo_email, stack_position=2),
        ])
        report = await harness.rollback()
        assert report.session_result == "compensated"
        assert report.compensation_order == ["email", "charge"]
    """

    def __init__(self, session_effects: Sequence[EffectDescriptor]) -> None:
        """Build a harness from recorded session effects.

        Args:
            session_effects: Effects to roll back, in any order.

        Raises:
            ValueError: If two entries share a ``stack_position``, or the
                list is empty.
        """
        if not session_effects:
            raise ValueError("session_effects must not be empty")
        positions = [e.stack_position for e in session_effects]
        if len(set(positions)) != len(positions):
            dupes = sorted({p for p in positions if positions.count(p) > 1})
            raise ValueError(f"duplicate stack_position values: {dupes}")
        self._effects = tuple(session_effects)
        self._last_report: SagaReport | None = None

    @property
    def session_effects(self) -> tuple[EffectDescriptor, ...]:
        """Recorded effects in construction order."""
        return self._effects

    async def rollback(self) -> SagaReport:
        """Execute the compensation pass in LIFO order.

        Each entry is attempted up to its retry budget. A permanent
        failure (an exception on the final attempt) stops the pass and
        yields a ``halted`` report; remaining entries are not invoked.

        Returns:
            A :class:`SagaReport` with per-step outcomes and the
            compensation invocation order. Also stored and retrievable
            via :meth:`report`.
        """
        ordered = sorted(self._effects, key=lambda e: e.stack_position, reverse=True)
        steps: list[StepResult] = []
        order: list[str] = []
        halted = False
        for effect in ordered:
            budget = (
                effect.max_retries if effect.max_retries > 0 else _DEFAULT_MAX_RETRIES
            )
            attempts = 0
            error: str | None = None
            compensated = False
            step_args: dict[str, Any] = {}
            if effect.kwargs:
                step_args.update(effect.kwargs)
            if effect.args:
                if len(effect.args) == 1 and isinstance(effect.args[0], dict):
                    step_args.update(effect.args[0])
                elif not effect.kwargs:
                    step_args["args"] = effect.args
                else:
                    step_args["_args"] = effect.args

            while attempts < budget:
                attempts += 1
                try:
                    await effect.compensation(*effect.args, **(effect.kwargs or {}))
                except Exception as exc:
                    error = str(exc)
                    log.debug(
                        "rollback attempt %d for %s failed: %s",
                        attempts,
                        effect.tool_name,
                        exc,
                    )
                else:
                    order.append(effect.tool_name)
                    compensated = True
                    break
            if compensated:
                steps.append(
                    StepResult(
                        tool_name=effect.tool_name,
                        stack_position=effect.stack_position,
                        attempts=attempts,
                        state="compensated",
                        args=step_args,
                        dead_letter=False,
                    )
                )
                continue
            log.warning(
                "compensation failed permanently tool=%s stack_position=%d",
                effect.tool_name,
                effect.stack_position,
            )
            steps.append(
                StepResult(
                    tool_name=effect.tool_name,
                    stack_position=effect.stack_position,
                    attempts=attempts,
                    state="failed",
                    error=error,
                    args=step_args,
                    dead_letter=True,
                )
            )
            halted = True
            break
        report = SagaReport(
            session_result=(
                _HALTED_SESSION_RESULT if halted else _DEFAULT_SESSION_RESULT
            ),
            steps=tuple(steps),
            compensation_order=tuple(order),
            dead_letters=tuple(s for s in steps if s.dead_letter),
        )
        self._last_report = report
        return report

    def report(self) -> SagaReport:
        """Return the report of the most recent :meth:`rollback`.

        Returns:
            The :class:`SagaReport` produced by the last rollback.

        Raises:
            RuntimeError: If :meth:`rollback` has not run yet.
        """
        if self._last_report is None:
            raise RuntimeError("rollback() has not run; no report available")
        return self._last_report


async def test_compensation(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    retries: int | None = None,
    fail_count: int = 0,
    backoff_ms: int = 100,
    backoff_multiplier: float = 2.0,
    max_backoff_ms: int = 30000,
    simulate_delay: bool = False,
    failure_exception: Exception | None = None,
    **kwargs: Any,
) -> CompensationReport:
    """Test one compensation function without a running engine.

    Executes the function, then verifies the idempotency contract by
    invoking it a second time with identical arguments and comparing
    results. The first and second executions must both succeed and
    agree for ``final_state`` to be ``"compensated"``.

    Supports transient network failure simulation, backoff interval
    tracking, and dead-letter state recording.

    Args:
        fn: The async compensation function under test.
        *args: Positional arguments for the compensation call.
        retries: Extra attempts allowed beyond the first call. When None,
            defaults to ``max(1, fail_count)``.
        fail_count: Number of simulated transient failures to inject
            before permitting successful execution.
        backoff_ms: Initial backoff interval in milliseconds.
        backoff_multiplier: Multiplier applied per retry attempt.
        max_backoff_ms: Maximum backoff ceiling in milliseconds.
        simulate_delay: When True, awaits actual backoff delay via asyncio.sleep.
        failure_exception: Custom exception raised for injected failures.
        **kwargs: Keyword arguments for the compensation call.

    Returns:
        A :class:`CompensationReport` with counts, final state, backoff intervals,
        and dead-letter status.
    """
    effective_retries = max(1, fail_count) if retries is None else retries
    max_attempts = effective_retries + 1
    harness = CompensationTestHarness(fn)
    result: Any = None
    attempts = 0
    final_state = "failed"
    error: str | None = None
    injected_failures = 0
    backoff_intervals: list[int] = []

    for _ in range(max_attempts):
        attempts += 1
        if injected_failures < fail_count:
            injected_failures += 1
            exc = failure_exception or NetworkDropoutError(
                f"Simulated transient network failure (attempt {attempts}/{max_attempts})"
            )
            error = str(exc)
            log.debug("test_compensation simulated failure %d: %s", attempts, exc)
            if attempts < max_attempts:
                interval = calculate_backoff(
                    attempts - 1,
                    base_backoff_ms=backoff_ms,
                    multiplier=backoff_multiplier,
                    max_backoff_ms=max_backoff_ms,
                )
                backoff_intervals.append(interval)
                if simulate_delay and interval > 0:
                    await asyncio.sleep(interval / 1000.0)
            continue

        try:
            result = await harness.execute(*args, **kwargs)
        except Exception as exc:
            error = str(exc)
            log.debug("test_compensation attempt %d failed: %s", attempts, exc)
            if attempts < max_attempts:
                interval = calculate_backoff(
                    attempts - 1,
                    base_backoff_ms=backoff_ms,
                    multiplier=backoff_multiplier,
                    max_backoff_ms=max_backoff_ms,
                )
                backoff_intervals.append(interval)
                if simulate_delay and interval > 0:
                    await asyncio.sleep(interval / 1000.0)
        else:
            final_state = "compensated"
            break

    retry_count = max(attempts - 1, 0)
    dead_letter = final_state == "failed"

    if final_state == "compensated" and effective_retries > 0:
        second: Any = None
        try:
            second = await harness.execute(*args, **kwargs)
        except Exception as exc:
            error = str(exc)
            final_state = "failed"
            dead_letter = True
        else:
            if second != result:
                final_state = "failed"
                dead_letter = True
                error = f"idempotency violation: first={result!r}, second={second!r}"

    return CompensationReport(
        fn_name=getattr(fn, "__name__", "?"),
        execution_count=harness.call_count,
        retry_count=retry_count,
        final_state=final_state,
        result=result,
        error=error,
        dead_letter=dead_letter,
        backoff_intervals_ms=tuple(backoff_intervals),
    )


# Not a pytest test: the test_ name is mandated by the public API contract.
setattr(test_compensation, "__test__", False)


def _assert_payload_parity(
    idx: int,
    tool_name: str,
    actual_args: dict[str, Any],
    expected_payload: Any,
) -> None:
    """Verify argument payload equality between actual step and expectation."""
    if isinstance(expected_payload, dict):
        if actual_args == expected_payload:
            return
        if all(actual_args.get(k) == v for k, v in expected_payload.items()):
            return
        raise AssertionError(
            f"Step {idx} ('{tool_name}') payload mismatch: expected {expected_payload!r}, got {actual_args!r}"
        )
    if isinstance(expected_payload, (tuple, list)):
        expected_tuple = tuple(expected_payload)
        if (
            actual_args.get("args") == expected_tuple
            or actual_args.get("_args") == expected_tuple
        ):
            return
        if tuple(actual_args.values()) == expected_tuple:
            return
        if len(expected_tuple) == 1 and expected_tuple[0] == actual_args:
            return
        raise AssertionError(
            f"Step {idx} ('{tool_name}') payload mismatch: expected {expected_payload!r}, got {actual_args!r}"
        )
    if len(actual_args) == 1 and next(iter(actual_args.values())) == expected_payload:
        return
    if actual_args.get("args") == (expected_payload,):
        return
    raise AssertionError(
        f"Step {idx} ('{tool_name}') payload mismatch: expected {expected_payload!r}, got {actual_args!r}"
    )


def assert_saga_rolled_back(
    session: SagaReport | SagaTestHarness | Any,
    expected_compensations: Sequence[
        str | tuple[str, Any] | dict[str, Any] | EffectDescriptor | StepResult
    ],
) -> None:
    """Assert that a saga session rolled back completely in LIFO order.

    Validates that:
        1. Rollback concluded with session_result == "compensated".
        2. No steps were routed to dead-letter state.
        3. Compensations executed in strict descending stack position (LIFO) order.
        4. Step count matches expected_compensations.
        5. Each executed compensation matches expected tool name and payload.

    Args:
        session: A :class:`SagaReport`, :class:`SagaTestHarness`, or any session
            object exposing a report method or report attribute.
        expected_compensations: Sequence of expected compensations. Each entry can be:
            - A string tool name (e.g. "email")
            - A tuple of (tool_name, expected_args)
            - A dict with "tool_name" and optional "args"
            - An :class:`EffectDescriptor` or :class:`StepResult`

    Raises:
        AssertionError: If session result is not compensated, dead letters exist,
            step count mismatches, execution order is not LIFO, or payload parity fails.
    """
    report: SagaReport
    if isinstance(session, SagaReport):
        report = session
    elif isinstance(session, SagaTestHarness):
        report = session.report()
    elif hasattr(session, "report") and callable(session.report):
        report = session.report()
    elif hasattr(session, "report") and isinstance(session.report, SagaReport):
        report = session.report
    elif hasattr(session, "steps") and hasattr(session, "session_result"):
        report = session
    else:
        raise AssertionError(
            f"Expected a SagaReport or SagaTestHarness, got {type(session).__name__}"
        )

    if report.session_result != "compensated":
        raise AssertionError(
            f"Expected saga rollback to succeed ('compensated'), but got '{report.session_result}'"
        )
    if report.dead_letters:
        dead_tools = [s.tool_name for s in report.dead_letters]
        raise AssertionError(
            f"Expected no dead-letter steps in rollback, but found: {dead_tools}"
        )

    steps = report.steps
    if len(steps) != len(expected_compensations):
        actual_names = [s.tool_name for s in steps]
        raise AssertionError(
            f"Expected {len(expected_compensations)} rolled back step(s), but got {len(steps)}: {actual_names}"
        )

    for idx, (step, expected) in enumerate(zip(steps, expected_compensations)):
        if idx > 0 and step.stack_position >= steps[idx - 1].stack_position:
            raise AssertionError(
                f"LIFO order violation at step {idx}: stack_position {step.stack_position} "
                f"must be less than preceding step position {steps[idx - 1].stack_position}"
            )

        expected_tool: str
        expected_payload: Any = None
        has_payload_expectation = False

        if isinstance(expected, str):
            expected_tool = expected
        elif isinstance(expected, (tuple, list)):
            if len(expected) >= 1:
                expected_tool = str(expected[0])
            if len(expected) >= 2:
                expected_payload = expected[1]
                has_payload_expectation = True
        elif isinstance(expected, dict):
            expected_tool = str(expected.get("tool_name") or expected.get("name") or "")
            if "args" in expected:
                expected_payload = expected["args"]
                has_payload_expectation = True
        elif hasattr(expected, "tool_name"):
            expected_tool = str(getattr(expected, "tool_name"))
            if hasattr(expected, "args"):
                expected_payload = getattr(expected, "args")
                has_payload_expectation = True
        else:
            raise AssertionError(
                f"Unsupported expected compensation specification: {expected!r}"
            )

        if step.tool_name != expected_tool:
            raise AssertionError(
                f"Step {idx} order mismatch: expected tool '{expected_tool}', got '{step.tool_name}'"
            )

        if step.state != "compensated":
            raise AssertionError(
                f"Step {idx} ('{step.tool_name}') failed during rollback with error: {step.error}"
            )

        if has_payload_expectation:
            _assert_payload_parity(idx, step.tool_name, step.args, expected_payload)


async def test_saga(
    session_effects: Sequence[EffectDescriptor],
) -> SagaReport:
    """Simulate a full LIFO compensation chain without a running engine.

    Convenience wrapper around :class:`SagaTestHarness`.

    Args:
        session_effects: Recorded effects to roll back, in any order.

    Returns:
        A :class:`SagaReport` with per-step outcomes and the
        compensation invocation order.

    Raises:
        ValueError: If two entries share a ``stack_position``.
    """
    harness = SagaTestHarness(session_effects)
    return await harness.rollback()


# Not a pytest test: the test_ name is mandated by the public API contract.
setattr(test_saga, "__test__", False)
