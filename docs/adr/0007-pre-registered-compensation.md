---
title: "ADR 0007: Pre-Registered Compensation for Crash Safety"
description: "- **Date:** 2026-02-28 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0007: Pre-Registered Compensation for Crash Safety

- **Date:** 2026-02-28
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

When an agent executes a tool call, UndoLog registers a compensation action (e.g., `delete_draft(id=42)`) so the effect can be undone if a later step fails. If the process crashes between executing the tool call and recording the compensation, the effect is orphaned, it has no registered undo action. This gap is the most dangerous failure mode for a saga-based rollback system: the effect was applied but cannot be automatically compensated.

## Decision

Register the compensation (push to the undo stack in the database) BEFORE executing the action. The undo entry starts in a `pending` state and transitions to `active` after the action completes successfully.

## Alternatives Considered

### Register After Execution (Post-Action Registration)

- **Pros:** Simpler flow: the compensation is only registered if the action actually succeeded. No `pending` state to manage. No risk of registering a compensation for an action that never started.
- **Cons:** Vulnerable to the crash-between-exec-and-register gap. This gap is precisely when the compensation is most needed, the effect has been applied and the process has no record of it. Recovery requires out-of-band reconciliation or manual intervention.
- **Chosen?** No. The vulnerability window is unacceptable for a system that guarantees effect reversibility.

### Register Before + Validate After (Two-Phase Registration)

- **Pros:** Strongest guarantee: the compensation is registered before execution, and the system validates post-execution that the compensation state matches the actual outcome. Any mismatch triggers an alert.
- **Cons:** Adds complexity to the registration flow: two state transitions per tool call instead of one. The validation step is an additional database write on the hot path.
- **Chosen?** No. The added complexity and latency are not justified. The simpler pre-registration approach captures the same safety property without the validation overhead.

### Deferred Registration with Periodic Reconciliation

- **Pros:** No change to the hot path: compensation registration happens asynchronously. A reconciler job fixes any gaps.
- **Cons:** Leaves the system exposed to the crash-between-exec-and-register gap for the entire reconciliation interval. Requires the reconciler to detect which effects are missing compensations, detection logic is itself complex and error-prone.
- **Chosen?** No. The time gap and detection complexity make this less reliable than pre-registration.

## Consequences

**Positive:** No gap between execution and registration: the compensation is always recorded before the action executes. Even if the process crashes between registration and execution, the compensation exists and can be applied (the saga orchestrator will call the compensation endpoint with the registered arguments). The `pending` state allows the orchestrator to distinguish between actions that are in-flight and actions that completed successfully.

**Negative:** If compensation registration succeeds but the action never starts (crash immediately after registration), the saga orchestrator will attempt to compensate an unexecuted effect. This is architecturally harmless, the compensation endpoint receives the same arguments but has nothing to undo. However, it may produce a log entry that appears alarming.

**Risks:** The `pending` to `active` transition is a critical state mutation that must be atomic. If the process crashes during this transition, the undo entry may remain in `pending` state permanently. A cleanup process is needed to detect and resolve stuck `pending` entries beyond a configurable timeout.
