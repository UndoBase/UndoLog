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

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


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
