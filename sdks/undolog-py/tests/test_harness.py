"""Tests for ``CompensationTestHarness``, ``SagaTestHarness``, and the
``test_compensation`` / ``test_saga`` top-level APIs.

Covers:
    - Execution counting and argument recording
    - Return value capture
    - Retry behaviour with ``execute_with_retry``
    - Idempotency verification via ``assert_idempotent``
    - Assertion helpers: ``assert_called_once``, ``assert_called_with``
    - Error recording and re-raise
    - Reset clears all state
    - LIFO rollback order via ``test_saga`` / ``SagaTestHarness``
    - Fail-fast halt on permanent compensation failure
    - Retry budget with the engine's 0-means-default contract
    - Input validation: duplicate and negative stack positions
    - Works in CI without a running engine or database
"""

from __future__ import annotations

from typing import Any

import pytest

from undolog_sdk import (
    EffectDescriptor,
    SagaReport,
    SagaTestHarness,
    StepResult,
    test_compensation,
    test_saga,
)
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


# ── SagaTestHarness: LIFO rollback ──────────────────────────────────────────


async def undo_charge(tx_id: str) -> dict[str, str]:
    """Fake compensation: reverse a charge."""
    return {"undone": tx_id}


class TestSagaLifoOrder:
    """Effects roll back in LIFO stack-position order."""

    async def test_lifo_order_regardless_of_input_order(self) -> None:
        calls: list[str] = []

        def _tracker(name: str) -> Any:
            async def comp() -> None:
                calls.append(name)

            return comp

        report = await test_saga(
            [
                EffectDescriptor("first", _tracker("first"), stack_position=1),
                EffectDescriptor("third", _tracker("third"), stack_position=3),
                EffectDescriptor("second", _tracker("second"), stack_position=2),
            ]
        )

        assert report.session_result == "compensated"
        assert report.compensation_order == ("third", "second", "first")
        assert calls == ["third", "second", "first"]

    async def test_report_lists_steps_in_rollback_order(self) -> None:
        report = await test_saga(
            [
                EffectDescriptor("charge", undo_charge, stack_position=1, args=("t1",)),
                EffectDescriptor(
                    "email", undo_send_email, stack_position=2, args=("a@x",)
                ),
            ]
        )

        assert [s.tool_name for s in report.steps] == ["email", "charge"]
        assert [s.stack_position for s in report.steps] == [2, 1]
        assert all(s.state == "compensated" for s in report.steps)

    async def test_input_order_preserved_in_session_effects(self) -> None:
        effects = [
            EffectDescriptor("b", undo_charge, stack_position=2, args=("t",)),
            EffectDescriptor("a", undo_charge, stack_position=1, args=("t",)),
        ]
        harness = SagaTestHarness(effects)

        assert [e.tool_name for e in harness.session_effects] == ["b", "a"]


class TestSagaFailFast:
    """A permanent failure halts the pass; lower entries are untouched."""

    async def test_halt_stops_remaining_entries(self) -> None:
        bottom_calls: list[str] = []
        top_calls: list[str] = []

        async def failing() -> None:
            raise ValueError("endpoint returned 404")

        async def track_bottom() -> None:
            bottom_calls.append("bottom")

        async def track_top() -> None:
            top_calls.append("top")

        report = await test_saga(
            [
                EffectDescriptor("bottom", track_bottom, stack_position=1),
                EffectDescriptor("broken", failing, stack_position=2),
                EffectDescriptor("top", track_top, stack_position=3),
            ]
        )

        # LIFO: top rolls back first, broken fails and halts the pass,
        # and bottom (lower stack position) is never invoked.
        assert report.session_result == "halted"
        assert report.compensation_order == ("top",)
        assert top_calls == ["top"]
        assert bottom_calls == []
        assert [(s.tool_name, s.state) for s in report.steps] == [
            ("top", "compensated"),
            ("broken", "failed"),
        ]
        assert report.steps[1].error == "endpoint returned 404"

    async def test_halt_at_highest_position_rolls_nothing(self) -> None:
        calls: list[str] = []

        async def failing() -> None:
            raise RuntimeError("permanent")

        async def never_called() -> None:
            calls.append("never")

        report = await test_saga(
            [
                EffectDescriptor("charge", never_called, stack_position=1),
                EffectDescriptor("broken", failing, stack_position=2),
            ]
        )

        # The failure sits at the top of the stack, so nothing else runs.
        assert report.session_result == "halted"
        assert report.compensation_order == ()
        assert [(s.tool_name, s.state) for s in report.steps] == [("broken", "failed")]
        assert calls == []


class TestSagaRetryBudget:
    """Per-entry retry budgets with the 0-means-default contract."""

    async def test_transient_failure_recovers_within_budget(self) -> None:
        count = 0

        async def flaky() -> None:
            nonlocal count
            count += 1
            if count < 3:
                raise RuntimeError("transient")

        report = await test_saga([EffectDescriptor("flaky", flaky, stack_position=1)])

        assert report.session_result == "compensated"
        assert report.steps[0].attempts == 3

    async def test_zero_max_retries_means_default_budget(self) -> None:
        count = 0

        async def fails_twice() -> None:
            nonlocal count
            count += 1
            if count < 3:
                raise RuntimeError("transient")

        report = await test_saga(
            [EffectDescriptor("e", fails_twice, stack_position=1, max_retries=0)]
        )

        assert report.session_result == "compensated"
        assert report.steps[0].attempts == 3

    async def test_exhausted_budget_records_error(self) -> None:
        attempts_seen: list[int] = []

        async def always_fails_fn() -> None:
            attempts_seen.append(1)
            raise ValueError("nope")

        report = await test_saga(
            [EffectDescriptor("e", always_fails_fn, stack_position=1, max_retries=2)]
        )

        assert report.session_result == "halted"
        assert report.steps[0].attempts == 2
        assert len(attempts_seen) == 2


class TestSagaValidation:
    """Construction-time input validation."""

    async def test_duplicate_stack_positions_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate stack_position"):
            SagaTestHarness(
                [
                    EffectDescriptor("a", undo_charge, stack_position=1, args=("t",)),
                    EffectDescriptor("b", undo_charge, stack_position=1, args=("t",)),
                ]
            )

    async def test_negative_stack_position_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            EffectDescriptor("a", undo_charge, stack_position=-1, args=("t",))

    async def test_empty_effects_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SagaTestHarness([])

    async def test_report_before_rollback_raises(self) -> None:
        harness = SagaTestHarness(
            [EffectDescriptor("a", undo_charge, stack_position=1, args=("t",))]
        )

        with pytest.raises(RuntimeError, match=r"rollback\(\) has not run"):
            harness.report()

    async def test_report_after_rollback_returns_report(self) -> None:
        harness = SagaTestHarness(
            [EffectDescriptor("a", undo_charge, stack_position=1, args=("t",))]
        )
        await harness.rollback()

        assert harness.report().session_result == "compensated"


# ── test_compensation top-level API ─────────────────────────────────────────


class TestCompensationApi:
    """``test_compensation`` reports counts, state, and idempotency."""

    async def test_successful_idempotent_compensation(self) -> None:
        report = await test_compensation(undo_transfer, "tx-123")

        assert report.final_state == "compensated"
        assert report.execution_count == 2
        assert report.retry_count == 0
        assert report.result == {"undone": "tx-123"}
        assert report.error is None

    async def test_failing_compensation_reports_error(self) -> None:
        async def bad(tx_id: str) -> dict[str, str]:
            raise ValueError("downstream down")

        report = await test_compensation(bad, "tx-1")

        assert report.final_state == "failed"
        assert report.retry_count == 1
        assert report.execution_count == 0
        assert "downstream down" in (report.error or "")

    async def test_non_idempotent_compensation_fails(self) -> None:
        counter = 0

        async def non_idempotent(tx_id: str) -> int:
            nonlocal counter
            counter += 1
            return counter

        report = await test_compensation(non_idempotent, "tx-1")

        assert report.final_state == "failed"
        assert "idempotency violation" in (report.error or "")

    async def test_kwargs_forwarded(self) -> None:
        report = await test_compensation(undo_send_email, to="a@x.com", subject="Hi")

        assert report.final_state == "compensated"
        assert report.result == {"status": "undone", "to": "a@x.com"}

    async def test_fn_name_recorded(self) -> None:
        report = await test_compensation(undo_transfer, "tx-1")

        assert report.fn_name == "undo_transfer"


# ── Report value semantics ──────────────────────────────────────────────────


class TestReportSemantics:
    """Frozen dataclass reports support equality and immutability."""

    async def test_saga_report_equality(self) -> None:
        left = await test_saga(
            [EffectDescriptor("a", undo_charge, stack_position=1, args=("t",))]
        )
        right = await test_saga(
            [EffectDescriptor("a", undo_charge, stack_position=1, args=("t",))]
        )

        assert left == right

    async def test_step_result_frozen(self) -> None:
        step = StepResult(tool_name="a", stack_position=1, attempts=1, state="ok")

        with pytest.raises(AttributeError):
            step.tool_name = "b"  # type: ignore[misc]

    async def test_saga_report_type(self) -> None:
        report = await test_saga(
            [EffectDescriptor("a", undo_charge, stack_position=1, args=("t",))]
        )

        assert isinstance(report, SagaReport)
