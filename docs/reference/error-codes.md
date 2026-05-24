---
title: "Error Codes Reference"
description: "Errors produced by the UndoLog runtime across all crates. The `UndoLogError` enum is defined in `crates/undolog-types/src/errors.rs`."
section: "reference"
---
# Error Codes Reference

Errors produced by the UndoLog runtime across all crates. The `UndoLogError` enum is defined in `crates/undolog-types/src/errors.rs`.

**Rust type:** `undolog_types::errors::UndoLogError`  
**Result alias:** `UndoLogResult<T> = Result<T, UndoLogError>`

---

## Error Enum

### `ToolNotRegistered`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::ToolNotRegistered { tool_name, org_id }` |
| Message | `Tool '{tool_name}' is not registered for org '{org_id}'` |

**When raised:** The engine receives a tool call for a tool name that does not exist in `undolog_tool_registry` for the given organisation.

**Example scenario:** An agent calls `delete_invoice` but the tool was never registered via the SDK annotation.

**How to handle:** Register the tool using the SDK `@undolog_tool` decorator. Verify the tool name matches the registry entry. Check organisation ID is correct.

---

### `DuplicateSignature`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::DuplicateSignature { signature }` |
| Message | `Call signature conflict: effect with signature '{signature}' already exists` |

**When raised:** An INSERT on `undolog_effect_log` conflicts with an existing `call_signature` and the advisory lock already prevented the race. This error is returned when the engine cannot resolve the conflict automatically.

**Example scenario:** Two concurrent proxy instances attempt to insert the same tool call simultaneously and the advisory lock + UNIQUE constraint interact unexpectedly.

**How to handle:** This is a safety-net error. Under normal operation the `ON CONFLICT DO NOTHING` + Replay path handles duplicates. If raised, retry the call or check for session state corruption.

---

### `InvalidStateTransition`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::InvalidStateTransition { effect_id, current_state, target_state }` |
| Message | `Effect '{effect_id}' is in state '{current_state}', cannot transition to '{target_state}'` |

**When raised:** An attempt is made to transition an effect to a state that is not reachable from its current state per the state machine rules.

**Example scenario:** Calling Commit on an effect that is in `Pending` state instead of `Executing`.

**How to handle:** Verify the effect's current state via the effect log. Ensure the correct RPC sequence is followed: Intercept → execute → Commit/Fail. Do not call Commit twice.

---

### `NotExecuting`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::NotExecuting { effect_id }` |
| Message | `Cannot commit effect '{effect_id}': not in 'executing' state` |

**When raised:** Commit is called on an effect that is not in the `Executing` state.

**Example scenario:** The proxy calls Commit after a replay (effect is already `Committed` or `Replayed`).

**How to handle:** Only call Commit after receiving an `Execute` outcome from Intercept. Do not call Commit for Replay or AwaitingApproval outcomes.

---

### `CompensationFailed`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::CompensationFailed { fn_name, retries, reason }` |
| Message | `Compensation '{fn_name}' failed after {retries} retries: {reason}` |

**When raised:** The compensation function for a Compensable tool exhausted all retry attempts without succeeding.

**Example scenario:** `undo_send_email` compensation called after a session failure, but the upstream email API is down and all 3 retries failed.

**How to handle:** Manual intervention is required. Inspect the compensation registry and the upstream service. Resolve the underlying issue, then retry compensation manually or mark the session as compensated.

---

### `EmptyUndoStack`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::EmptyUndoStack { session_id }` |
| Message | `Undo stack for session '{session_id}' is empty` |

**When raised:** The saga orchestrator attempts to pop from the undo stack, but there are no pending compensation entries for the session.

**Example scenario:** A session fails but no Compensable tools were executed (only Safe and Irreversible tools).

**How to handle:** This is an informational error. No compensation is needed if the undo stack is empty.

---

### `CompensationRegisteredTooLate`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::CompensationRegisteredTooLate { effect_id }` |
| Message | `Compensation registered after action executed for effect '{effect_id}', invariant violated` |

**When raised:** The saga orchestrator detects that a compensation entry was registered after the tool execution timestamp, violating the core safety invariant.

**Example scenario:** A bug in the orchestrator causes the undo stack push to happen after the tool call instead of before.

**How to handle:** This indicates a bug in the orchestrator logic. The invariant `registered_at < executed_at` must always hold. Fix the ordering in the orchestrator code.

---

### `ApprovalAlreadyResolved`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::ApprovalAlreadyResolved { approval_id }` |
| Message | `Approval request '{approval_id}' has already been resolved` |

**When raised:** An Approve or Reject RPC is called for an approval request that is already in a terminal state.

**Example scenario:** Two dashboard users both click "Approve" on the same approval request. The second request returns this error.

**How to handle:** The approval has already been processed. The caller should refresh the approval list and check the current state.

---

### `ApprovalTimedOut`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::ApprovalTimedOut { approval_id }` |
| Message | `Approval request '{approval_id}' timed out` |

**When raised:** The approval window expired before a human decision was made. The approval record transitions to `TimedOut` or `AutoApproved` depending on org policy.

**Example scenario:** An approval request was created with `timeout_at` of 1 hour. No decision was made within that window.

**How to handle:** If `auto_approve_on_timeout` is `true`, the tool auto-executes. If `false`, the agent must be re-invoked with a new session.

---

### `AdvisoryLockTimeout`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::AdvisoryLockTimeout { signature, attempts }` |
| Message | `Could not acquire advisory lock for signature '{signature}' after {attempts} attempts` |

**When raised:** The engine could not acquire a PostgreSQL advisory lock for a call signature after `UNDOLOG_LOCK_MAX_ATTEMPTS` attempts.

**Example scenario:** High concurrency on the same tool call signature, causing repeated lock collisions.

**How to handle:** Retry the operation. If persistent, increase `UNDOLOG_LOCK_MAX_ATTEMPTS` or `UNDOLOG_LOCK_RETRY_MS`. The lock is only held for the duration of the INSERT transaction (typically < 100ms).

---

### `Database`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::Database(sqlx::Error)` |
| Message | `Database error: {0}` |

**When raised:** Any SQL-level error from PostgreSQL: connection failure, constraint violation, deadlock, serialisation failure, partition missing, etc.

**Example scenario:** PostgreSQL connection pool exhausted during a traffic spike. A query violates a CHECK constraint.

**How to handle:** Check database connectivity and pool settings. Inspect the inner `sqlx::Error` for the specific SQL error code. Most database errors are transient and can be retried.

---

### `Serialization`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::Serialization(serde_json::Error)` |
| Message | `Serialization error: {0}` |

**When raised:** JSON serialisation or deserialisation fails for internal data structures (tool calls, results, compensation args, etc.).

**Example scenario:** A tool returns non-UTF-8 output that cannot be serialised. Internal enum variant does not match the JSON representation.

**How to handle:** Validate tool inputs and outputs are valid JSON. Check for version mismatches between the proxy and engine (new enum variants that the other side does not know about).

---

### `Internal`

| Property | Value |
|----------|-------|
| Variant | `UndoLogError::Internal(String)` |
| Message | `Internal error: {0}` |

**When raised:** Any unexpected runtime error that does not fit into the above categories. Catch-all for programming errors.

**Example scenario:** A failed `tokio::spawn` join handle. An unexpected `None` where a value was expected.

**How to handle:** This is a bug. Report the error message along with the session and effect IDs for debugging.

---

## Error Handling Quick Reference

| Error | Retryable | Manual intervention | Check |
|-------|-----------|-------------------|-------|
| `ToolNotRegistered` | No | Yes | Tool registry |
| `DuplicateSignature` | Yes | No | Retry call |
| `InvalidStateTransition` | No | Yes | Effect state |
| `NotExecuting` | No | Yes | Effect state |
| `CompensationFailed` | No | Yes | Upstream service |
| `EmptyUndoStack` | No | No | Informational |
| `CompensationRegisteredTooLate` | No | Yes | Orchestrator code |
| `ApprovalAlreadyResolved` | No | No | Refetch approvals |
| `ApprovalTimedOut` | No | Depends | Timeout policy |
| `AdvisoryLockTimeout` | Yes | No | Increase lock settings |
| `Database` | Yes | Depends | DB connectivity |
| `Serialization` | No | Yes | Input/output format |
| `Internal` | No | Yes | Report as bug |
