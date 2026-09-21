"""Tests for ``CompensationTestHarness``.

Covers:
    - Execution counting and argument recording
    - Return value capture
    - Retry behaviour with ``execute_with_retry``
    - Idempotency verification via ``assert_idempotent``
    - Assertion helpers: ``assert_called_once``, ``assert_called_with``
    - Error recording and re-raise
    - Reset clears all state
    - Works in CI without a running engine or database
"""

from __future__ import annotations

from typing import Any

import pytest

from undolog_sdk.test_harness import CompensationTestHarness

# ── Test compensation functions ─────────────────────────────────────────────


async def undo_send_email(to: str, subject: str = "") -> dict[str, str]:
    """Fake compensation: undo an email send."""
    return {"status": "undone", "to": to}


async def undo_transfer(tx_id: str) -> dict[str, str]:
    """Fake compensation: undo a funds transfer."""
    return {"undone": tx_id}


def _make_flaky_compensation() -> Any:
    """Create a compensation that fails on the first call, then succeeds."""
    call_count = 0

    async def flaky_compensation(tx_id: str) -> dict[str, str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure")
        return {"undone": tx_id}

    return flaky_compensation


async def always_fails() -> None:
    """Compensation that always raises."""
    raise ValueError("permanent failure")


# ── Execution counting ─────────────────────────────────────────────────────


class TestExecutionCounting:
    """Harness tracks invocation count accurately."""

    async def test_initial_call_count_is_zero(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        assert harness.call_count == 0

    async def test_call_count_increments(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        await harness.execute("alice@example.com")
        assert harness.call_count == 1
        await harness.execute("bob@example.com")
        assert harness.call_count == 2

    async def test_multiple_calls_recorded(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        await harness.execute("tx-1")
        await harness.execute("tx-2")
        await harness.execute("tx-3")
        assert len(harness.calls) == 3
        assert len(harness.results) == 3


# ── Argument recording ─────────────────────────────────────────────────────


class TestArgumentRecording:
    """Harness records positional and keyword arguments for each call."""

    async def test_positional_args_recorded(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        await harness.execute("alice@example.com", "Hello")
        args, kwargs = harness.calls[0]
        assert args == ("alice@example.com", "Hello")
        assert kwargs == {}

    async def test_keyword_args_recorded(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        await harness.execute(to="alice@example.com", subject="Hello")
        args, kwargs = harness.calls[0]
        assert args == ()
        assert kwargs == {"to": "alice@example.com", "subject": "Hello"}

    async def test_mixed_args_recorded(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        await harness.execute("alice@example.com", subject="Hello")
        args, kwargs = harness.calls[0]
        assert args == ("alice@example.com",)
        assert kwargs == {"subject": "Hello"}


# ── Return values ───────────────────────────────────────────────────────────


class TestReturnValues:
    """Harness captures return values from the compensation function."""

    async def test_returns_compensation_result(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        result = await harness.execute("tx-123")
        assert result == {"undone": "tx-123"}

    async def test_results_list_matches_calls(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        await harness.execute("tx-1")
        await harness.execute("tx-2")
        assert harness.results == [{"undone": "tx-1"}, {"undone": "tx-2"}]


# ── Retry behaviour ─────────────────────────────────────────────────────────


class TestRetryBehaviour:
    """``execute_with_retry`` retries on failure and gives up after max."""

    async def test_succeeds_on_first_attempt(self) -> None:
        harness = CompensationTestHarness(undo_transfer, max_retries=3)
        result = await harness.execute_with_retry("tx-123")
        assert result == {"undone": "tx-123"}
        assert harness.call_count == 1

    async def test_retries_on_failure(self) -> None:
        flaky = _make_flaky_compensation()
        harness = CompensationTestHarness(flaky, max_retries=3)
        result = await harness.execute_with_retry("tx-456")
        assert result == {"undone": "tx-456"}
        assert harness.call_count == 1
        assert len(harness.errors) == 1

    async def test_raises_after_max_retries(self) -> None:
        harness = CompensationTestHarness(always_fails, max_retries=2)
        with pytest.raises(ValueError, match="permanent failure"):
            await harness.execute_with_retry()
        assert harness.call_count == 0
        assert len(harness.errors) == 2


# ── Idempotency ─────────────────────────────────────────────────────────────


class TestIdempotency:
    """``assert_idempotent`` verifies same args produce same result."""

    async def test_idempotent_function_passes(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        await harness.assert_idempotent("tx-123")
        assert harness.call_count == 2

    async def test_non_idempotent_function_fails(self) -> None:
        call_count = 0

        async def non_idempotent(tx_id: str) -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        harness = CompensationTestHarness(non_idempotent)
        with pytest.raises(AssertionError, match="idempotency violation"):
            await harness.assert_idempotent("tx-123")


# ── Assertion helpers ───────────────────────────────────────────────────────


class TestAssertionHelpers:
    """Built-in assertion methods for common test patterns."""

    async def test_assert_called_once_passes(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        await harness.execute("tx-123")
        harness.assert_called_once()

    async def test_assert_called_once_fails_on_zero(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        with pytest.raises(AssertionError, match="expected 1 call, got 0"):
            harness.assert_called_once()

    async def test_assert_called_once_fails_on_two(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        await harness.execute("tx-1")
        await harness.execute("tx-2")
        with pytest.raises(AssertionError, match="expected 1 call, got 2"):
            harness.assert_called_once()

    async def test_assert_called_with_passes(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        await harness.execute("alice@example.com", subject="Hi")
        harness.assert_called_with("alice@example.com", subject="Hi")

    async def test_assert_called_with_fails_on_wrong_args(self) -> None:
        harness = CompensationTestHarness(undo_send_email)
        await harness.execute("alice@example.com")
        with pytest.raises(AssertionError, match="positional args mismatch"):
            harness.assert_called_with("bob@example.com")

    async def test_assert_called_with_fails_when_not_called(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        with pytest.raises(AssertionError, match="never called"):
            harness.assert_called_with("tx-123")


# ── Error handling ──────────────────────────────────────────────────────────


class TestErrorHandling:
    """Errors are recorded and re-raised without swallowing."""

    async def test_error_recorded_in_errors_list(self) -> None:
        harness = CompensationTestHarness(always_fails, max_retries=1)
        with pytest.raises(ValueError, match="permanent failure"):
            await harness.execute()
        assert len(harness.errors) == 1
        assert isinstance(harness.errors[0][1], ValueError)

    async def test_error_does_not_increment_call_count(self) -> None:
        harness = CompensationTestHarness(always_fails)
        with pytest.raises(ValueError):
            await harness.execute()
        assert harness.call_count == 0


# ── Reset ───────────────────────────────────────────────────────────────────


class TestReset:
    """``reset()`` clears all recorded state."""

    async def test_reset_clears_counts(self) -> None:
        harness = CompensationTestHarness(undo_transfer)
        await harness.execute("tx-1")
        await harness.execute("tx-2")
        harness.reset()
        assert harness.call_count == 0
        assert harness.calls == []
        assert harness.results == []
        assert harness.errors == []

    async def test_reset_preserves_fn(self) -> None:
        harness = CompensationTestHarness(undo_transfer, max_retries=5)
        await harness.execute("tx-1")
        harness.reset()
        assert harness.fn is undo_transfer
        assert harness.max_retries == 5


# ── send_email compensation example ─────────────────────────────────────────


class TestSendEmailExample:
    """Example: test a ``send_email`` compensation end-to-end."""

    async def test_undo_send_email(self) -> None:
        sent: list[str] = []

        async def send_email(to: str, subject: str) -> dict[str, str]:
            sent.append(to)
            return {"status": "sent", "to": to}

        async def undo_send_email(to: str) -> dict[str, str]:
            return {"status": "undone", "to": to}

        # Simulate: send email, then compensate.
        await send_email("alice@example.com", subject="Welcome")
        assert sent == ["alice@example.com"]

        harness = CompensationTestHarness(undo_send_email)
        result = await harness.execute("alice@example.com")
        assert result == {"status": "undone", "to": "alice@example.com"}
        harness.assert_called_once()
