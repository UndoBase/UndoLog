---
title: "Effect States Reference"
description: "An effect represents a single tool call in the UndoLog effect log. Each effect transitions through a state machine that governs its lifecycle."
section: "reference"
---
# Effect States Reference

An effect represents a single tool call in the UndoLog effect log. Each effect transitions through a state machine that governs its lifecycle.

---

## State Machine Diagram

```
                                  ┌──────────────────────────────┐
                                  │                              │
                                  v                              │
  ┌─────────┐    intercept    ┌───────────┐  commit    ┌──────────┐
  │ Pending │ ───────────────>│ Executing │ ──────────>│ Committed│ (terminal)
  └─────────┘                 └───────────┘            └──────────┘
       │                            │
       │ (irreversible)             │ (fail)
       v                            v
  ┌───────────┐               ┌───────────┐
  │ Approved  │               │  Pending  │ (retry)
  └───────────┘               └───────────┘
       │                            │
       │ (approve)                  │ (LIFO undo)
       v                            v
  ┌───────────┐               ┌──────────────┐
  │ Executing │ ──> ...       │ Compensating │
  └───────────┘               └──────────────┘
       │                            │
       │                            ├──────────────────┐
       v                            v                  v
  ┌───────────┐               ┌──────────────┐   ┌───────────────────┐
  │ Rejected  │ (terminal)    │ Compensated  │   │CompensationFailed │
  └───────────┘               │  (terminal)  │   │   (terminal)      │
                              └──────────────┘   └───────────────────┘
  ┌───────────┐
  │ Replayed  │ (terminal)
  └───────────┘
```

---

## States

### `Pending`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Pending` |
| DB value | `'pending'` |
| Terminal | No |
| Description | Registered in the effect log; not yet executing. |

Initial state for all intercepted tool calls. The effect record is created with state `pending` before execution begins.

---

### `Executing`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Executing` |
| DB value | `'executing'` |
| Terminal | No |
| Description | Currently executing. |

Set when the proxy begins executing the tool call upstream. For `Safe` tier, this state is never reached.

---

### `Committed`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Committed` |
| DB value | `'committed'` |
| Terminal | Yes |
| Description | Completed successfully; result cached. |

Terminal success state. The `result_snapshot` is populated with the tool output. The effect is available for replay.

---

### `Compensating`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Compensating` |
| DB value | `'compensating'` |
| Terminal | No |
| Description | Compensation function is running. |

Set when the saga orchestrator begins executing the compensation function. Occurs during LIFO rollback of the undo stack.

---

### `Compensated`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Compensated` |
| DB value | `'compensated'` |
| Terminal | Yes |
| Description | Compensation completed; step has been undone. |

Terminal state. The compensation function completed successfully. The session's undo stack entry for this effect is also marked `compensated`.

---

### `CompensationFailed`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::CompensationFailed` |
| DB value | `'compensation_failed'` |
| Terminal | Yes |
| Description | Compensation failed permanently; requires manual intervention. |

Terminal failure state. The compensation function exhausted all retries without succeeding. Requires manual intervention to resolve.

---

### `Approved`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Approved` |
| DB value | `'approved'` |
| Terminal | No |
| Description | Human approved an Irreversible action; execution may proceed. |

Intermediate state for `Irreversible` tier tools. The human approved via the dashboard. The effect transitions to `Executing` when the proxy resumes.

---

### `Rejected`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Rejected` |
| DB value | `'rejected'` |
| Terminal | Yes |
| Description | Human rejected; session halted. |

Terminal state for `Irreversible` tier tools when the human rejects the action. The session transitions to `Halted`.

---

### `Replayed`

| Property | Value |
|----------|-------|
| Enum name | `EffectState::Replayed` |
| DB value | `'replayed'` |
| Terminal | Yes |
| Description | Result served from cache (exactly-once replay path). |

Terminal state. Set when the engine detects a duplicate `call_signature` and returns the cached result instead of executing. The `replay_count` and `last_replayed_at` fields are updated.

---

## Valid Transitions

| From | To | Trigger | Description |
|------|----|---------|-------------|
| `Pending` | `Executing` | Intercept → Execute | New tool call routed for execution. |
| `Pending` | `Approved` | Human approves via dashboard | Irreversible tool approved, awaiting proxy to resume. |
| `Pending` | `Rejected` | Human rejects via dashboard | Irreversible tool rejected, session halted. |
| `Pending` | `Replayed` | Intercept → Replay | Duplicate call signature detected, cached result returned. |
| `Executing` | `Committed` | Commit RPC | Tool executed successfully. |
| `Executing` | `Pending` | Fail RPC | Tool execution failed. Effect is reset to retry. |
| `Executing` | `Compensating` | Saga orchestrator on session failure | LIFO undo of a Compensable effect. |
| `Approved` | `Executing` | Proxy resumes tool call | Irreversible tool starts execution after approval. |
| `Compensating` | `Compensated` | Compensation succeeds | Undo completed successfully. |
| `Compensating` | `CompensationFailed` | Compensation exhausted retries | Permanent compensation failure. |

---

## Invalid Transitions

| From | To | Reason |
|------|----|--------|
| `Committed` | any state | Terminal state. No further transitions. |
| `Compensated` | any state | Terminal state. No further transitions. |
| `CompensationFailed` | any state | Terminal state. No further transitions. |
| `Rejected` | any state | Terminal state. No further transitions. |
| `Replayed` | any state | Terminal state. No further transitions. |
| `Pending` | `Compensating` | Compensation requires prior execution. |
| `Committed` | `Compensating` | Compensation retry path not implemented. |
| `Executing` | `Approved` | Approval is only from Pending. |
| `Pending` | `Committed` | Must execute first. |

Invalid transitions return `UndoLogError::InvalidStateTransition`.

---

## Terminal States

| State | Cannot transition to | Significance |
|-------|---------------------|--------------|
| `Committed` | any | Effect completed successfully and result cached. |
| `Compensated` | any | Effect undone via compensation. |
| `CompensationFailed` | any | Compensation failed; requires manual intervention. |
| `Rejected` | any | Human rejected the irreversible action. |
| `Replayed` | any | Result served from cache; no execution occurred. |

Terminal check in Rust:

```rust
pub fn is_terminal(&self) -> bool {
    matches!(
        self,
        EffectState::Committed
            | EffectState::Compensated
            | EffectState::CompensationFailed
            | EffectState::Rejected
            | EffectState::Replayed,
    )
}
```

---

## State Machine Rules

| Rule | Description |
|------|-------------|
| Exactly-once | `call_signature UNIQUE` constraint prevents duplicate effects. ON CONFLICT returns the existing effect. |
| Immutable history | State transitions are append-only. Previous states are never overwritten. |
| No skipping | Every transition must go through the defined path (e.g. `Pending → Executing → Committed`). |
| Compensable only | `Compensating` / `Compensated` / `CompensationFailed` are only reachable for `Compensable` tier effects. |
| Irreversible only | `Approved` / `Rejected` are only reachable for `Irreversible` tier effects. |
| Safe tier | Never enters the effect state machine. Executes directly without logging. |
