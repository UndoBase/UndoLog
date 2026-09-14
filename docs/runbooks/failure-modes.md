---
title: "Failure Mode Runbook"
description: "Maps each error code to cause, detection, recovery steps, and prevention."
section: "runbooks"
---
# Failure Mode Runbook

Maps each error code to cause, detection, recovery steps, and prevention.

---

## Tool errors

### `ToolNotRegistered`

| Field | Value |
|-------|-------|
| Cause | Tool call for a name not in `undolog_tool_registry` for the org |
| Detection | Engine returns `ToolNotRegistered` gRPC error |
| Recovery | Register the tool via SDK `@undolog_tool` decorator |
| Prevention | Validate tool names match registry entries before deployment |

---

### `DuplicateSignature`

| Field | Value |
|-------|-------|
| Cause | Concurrent proxy instances insert the same call signature |
| Detection | Engine returns `DuplicateSignature` gRPC error |
| Recovery | Retry the call; the advisory lock + ON CONFLICT handles most cases |
| Prevention | Ensure single-proxy deployment or use advisory lock tuning |

---

### `InvalidStateTransition`

| Field | Value |
|-------|-------|
| Cause | RPC called on effect in wrong state (e.g. Commit on Pending) |
| Detection | Engine returns `InvalidStateTransition` gRPC error |
| Recovery | Check effect state via effect log; follow correct RPC sequence |
| Prevention | Enforce Intercept -> Execute -> Commit/Fail sequence in proxy |

---

### `NotExecuting`

| Field | Value |
|-------|-------|
| Cause | Commit called on effect not in `Executing` state |
| Detection | Engine returns `NotExecuting` gRPC error |
| Recovery | Only call Commit after receiving Execute outcome from Intercept |
| Prevention | Track effect state in proxy; skip Commit for Replay/Approval outcomes |

---

### `EmptyUndoStack`

| Field | Value |
|-------|-------|
| Cause | Saga orchestrator pops from empty undo stack |
| Detection | Engine returns `EmptyUndoStack` gRPC error |
| Recovery | Informational; no compensation needed if stack is empty |
| Prevention | Check undo stack length before attempting rollback |

---

### `ApprovalAlreadyResolved`

| Field | Value |
|-------|-------|
| Cause | Approve/Reject called on already-resolved approval |
| Detection | Engine returns `ApprovalAlreadyResolved` gRPC error |
| Recovery | Refresh approval list; check current state |
| Prevention | Use atomic compare-and-swap for approval decisions |

---

### `ApprovalTimedOut`

| Field | Value |
|-------|-------|
| Cause | Approval window expired before human decision |
| Detection | Engine returns `ApprovalTimedOut` gRPC error |
| Recovery | If `auto_approve_on_timeout` is true, tool auto-executes; otherwise re-invoke agent |
| Prevention | Set appropriate timeout windows; monitor pending approvals |

---

### `AdvisoryLockTimeout`

| Field | Value |
|-------|-------|
| Cause | Could not acquire PostgreSQL advisory lock after max attempts |
| Detection | Engine returns `AdvisoryLockTimeout` gRPC error |
| Recovery | Retry the operation; increase `UNDOLOG_LOCK_MAX_ATTEMPTS` or `UNDOLOG_LOCK_RETRY_MS` |
| Prevention | Tune lock settings for expected concurrency; monitor lock contention |

---

### `Database`

| Field | Value |
|-------|-------|
| Cause | SQL-level error: connection failure, constraint violation, deadlock |
| Detection | Engine returns `Database` gRPC error with inner `sqlx::Error` |
| Recovery | Check database connectivity; inspect inner error for SQL error code |
| Prevention | Monitor connection pool usage; set appropriate pool size; use connection retry |

---

### `Serialization`

| Field | Value |
|-------|-------|
| Cause | JSON serialization/deserialization fails for internal data |
| Detection | Engine returns `Serialization` gRPC error with inner `serde_json::Error` |
| Recovery | Validate tool inputs/outputs are valid JSON; check version compatibility |
| Prevention | Ensure proxy and engine versions match; validate JSON at SDK boundary |

---

### `Internal`

| Field | Value |
|-------|-------|
| Cause | Unexpected runtime error not fitting other categories |
| Detection | Engine returns `Internal` gRPC error |
| Recovery | Report as bug with session and effect IDs for debugging |
| Prevention | Comprehensive testing; error handling for all edge cases |

---

## Compensation failures

### `CompensationRegisteredTooLate`

| Field | Value |
|-------|-------|
| Cause | Compensation registered after tool execution timestamp |
| Detection | Engine returns `CompensationRegisteredTooLate` gRPC error |
| Recovery | Fix ordering in orchestrator code; `registered_at < executed_at` must hold |
| Prevention | Enforce compensation registration before execution in orchestrator |

---

### `CompensationFailed`

| Field | Value |
|-------|-------|
| Cause | Compensation function exhausted all retries |
| Detection | Effect enters `CompensationFailed` terminal state |
| Recovery | Manual intervention required; inspect upstream service; retry compensation manually |
| Prevention | Ensure compensation functions are idempotent; set appropriate retry count |

---

## Infrastructure failures

### Database unavailable

| Field | Value |
|-------|-------|
| Cause | PostgreSQL connection pool exhausted, server down, network partition |
| Detection | Engine returns `Database` errors; health check fails |
| Recovery | Check PostgreSQL status; verify connection string; restart if needed |
| Prevention | Use connection pooling; monitor database health; set appropriate timeouts |

---

### Engine gRPC service down

| Field | Value |
|-------|-------|
| Cause | Engine process crashed, OOM, or not responding |
| Detection | Proxy returns 502/504 errors; gRPC status `Unavailable` |
| Recovery | Restart engine process; check logs for crash reason |
| Prevention | Use health checks; set resource limits; monitor memory usage |

---

### Proxy HTTP service down

| Field | Value |
|-------|-------|
| Cause | Proxy process crashed, OOM, or not responding |
| Detection | Client receives connection refused or timeout |
| Recovery | Restart proxy process; check logs for crash reason |
| Prevention | Use health checks; set resource limits; monitor memory usage |

---

### Network partition

| Field | Value |
|-------|-------|
| Cause | Network between proxy and engine is disrupted |
| Detection | gRPC calls timeout; health checks fail |
| Recovery | Wait for network recovery; check firewall rules; verify DNS resolution |
| Prevention | Use multiple availability zones; monitor network latency; set appropriate timeouts |

---

## See also

- [Error codes reference](../reference/error-codes.md): full error enum documentation
- [Effect states](../reference/effect-states.md): state machine transitions
- [Configuration reference](../reference/configuration.md): environment variables
