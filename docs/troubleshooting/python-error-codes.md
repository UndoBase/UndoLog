---
title: "Python SDK Error Codes and Troubleshooting Runbooks"
description: "Reference guide and actionable remediation runbooks for UndoLog Python SDK errors."
section: "troubleshooting"
---

# Python SDK Error Codes and Troubleshooting Runbooks

Every exception raised by the UndoLog Python SDK inherits from `UndoLogError`.
Each exception instance provides:

1. `code`: A machine-readable string identifier for programmatic error routing.
2. `message`: A descriptive human-readable explanation of the error.
3. `details`: A dictionary containing structured runtime context.
4. `runbook_url`: A direct link to the corresponding troubleshooting runbook.

---

## Quick Reference Table

| Error Code | Python Class | Retryable | Runbook Anchor |
| :--- | :--- | :--- | :--- |
| `AUTHENTICATION_FAILED` | `AuthenticationError` | No | [#authentication-failed](#authentication-failed) |
| `AWAITING_APPROVAL` | `AwaitingApprovalError` | No | [#awaiting-approval](#awaiting-approval) |
| `COMPENSATION_FAILED` | `CompensationError` | Yes | [#compensation-failed](#compensation-failed) |
| `CONFIGURATION_INVALID` | `ConfigurationError` | No | [#configuration-invalid](#configuration-invalid) |
| `CONNECTION_FAILED` | `ConnectionError` | Yes | [#connection-failed](#connection-failed) |
| `EFFECT_LOG_CONCURRENCY` | `EffectLogConcurrencyError` | Yes | [#effect-log-concurrency](#effect-log-concurrency) |
| `EFFECT_LOG_FAILED` | `EffectLogError` | Yes | [#effect-log-failed](#effect-log-failed) |
| `MISSING_SESSION` | `MissingSessionError` | No | [#missing-session](#missing-session) |
| `NOT_FOUND` | `NotFoundError` | No | [#not-found](#not-found) |
| `PERMISSION_DENIED` | `PermissionError` | No | [#permission-denied](#permission-denied) |
| `RATE_LIMITED` | `RateLimitError` | Yes | [#rate-limited](#rate-limited) |
| `SERIALIZATION_FAILED` | `SerializationError` | No | [#serialization-failed](#serialization-failed) |
| `SERVER_ERROR` | `ServerError` | Yes | [#server-error](#server-error) |
| `TIMEOUT` | `TimeoutError` | Yes | [#timeout](#timeout) |
| `TOOL_NOT_REGISTERED` | `ToolNotRegisteredError` | No | [#tool-not-registered](#tool-not-registered) |
| `TOOL_REGISTRATION_FAILED` | `ToolRegistrationError` | No | [#tool-registration-failed](#tool-registration-failed) |
| `INVALID_STATE_TRANSITION` | `InvalidStateTransitionError` | No | [#invalid-state-transition](#invalid-state-transition) |
| `APPROVAL_NOT_FOUND` | `ApprovalNotFoundError` | No | [#approval-not-found](#approval-not-found) |
| `VALIDATION_FAILED` | `ValidationError` | No | [#validation-failed](#validation-failed) |

---

## Logging Guidelines

When catching UndoLog exceptions, log the error code and runbook link using
standard Python logging handlers:

```python
import logging
from undolog_sdk import UndoLogError

logger = logging.getLogger(__name__)

try:
    await client.intercept(...)
except UndoLogError as err:
    logger.error(
        "UndoLog error [%s]: %s (Runbook: %s, Details: %s)",
        err.code,
        err.message,
        err.runbook_url,
        err.details,
    )
```

You can also use the built-in helper method `err.format_for_log()`:

```python
logger.error("Operation failed: %s", err.format_for_log())
```

---

## Error Runbooks

<span id="authentication-failed"></span>
### AUTHENTICATION_FAILED

**Class:** `AuthenticationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#authentication-failed`
**Description:** The API key or token supplied to the proxy is missing, expired, or invalid.

#### Common Causes

- The `UNDOLOG_API_KEY` environment variable is unset or empty.
- The supplied bearer token has expired.
- The credentials belong to an unauthorized organization.

#### Remediation Steps

1. Verify that `UNDOLOG_API_KEY` is present in your shell environment:
   ```bash
   echo "$UNDOLOG_API_KEY"
   ```
2. Initialize `UndoLogClient` with an explicit API key or base URL:
   ```python
   from undolog_sdk import UndoLogClient

   client = UndoLogClient(
       api_key="your_api_key",
       base_url="http://localhost:8080",
   )
   ```

---

<span id="awaiting-approval"></span>
### AWAITING_APPROVAL

**Class:** `AwaitingApprovalError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#awaiting-approval`
**Description:** An irreversible tool call was intercepted and requires human approval before proceeding.

#### Common Causes

- An agent invoked a tool classified as `ToolTier.IRREVERSIBLE`.
- The execution context has no active approval decision for this tool call.

#### Remediation Steps

1. Catch `AwaitingApprovalError` in your agent execution pipeline:
   ```python
   from undolog_sdk import AwaitingApprovalError

   try:
       await runner.execute(prompt)
   except AwaitingApprovalError as exc:
       print(f"Tool {exc.tool_name} requires approval. ID: {exc.approval_id}")
   ```
2. Resolve the approval request via dashboard or client `approve()` / `reject()` methods before resuming.

---

<span id="compensation-failed"></span>
### COMPENSATION_FAILED

**Class:** `CompensationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#compensation-failed`
**Description:** A compensating action failed after exhausting configured retry attempts.

#### Common Causes

- The compensating service is unreachable or experiencing an outage.
- Network timeouts exceeded retry limits during rollback.

#### Remediation Steps

1. Inspect the failed compensation name via `exc.fn_name` or `exc.details["fn_name"]`.
2. Check upstream service status and connectivity.
3. Review dead-letter records to resolve uncompensated effects manually.

---

<span id="configuration-invalid"></span>
### CONFIGURATION_INVALID

**Class:** `ConfigurationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#configuration-invalid`
**Description:** SDK or client configuration parameters are missing or invalid.

#### Common Causes

- An invalid URL scheme was passed to `UndoLogClient(base_url=...)`.
- Required environment variables are malformed.

#### Remediation Steps

1. Validate the specific configuration key identified in `exc.key` or `exc.details`.
2. Ensure proxy URLs include scheme and port (e.g. `http://localhost:8080`).

---

<span id="connection-failed"></span>
### CONNECTION_FAILED

**Class:** `ConnectionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#connection-failed`
**Description:** The Python SDK failed to establish an HTTP connection to the UndoLog proxy.

#### Common Causes

- The UndoLog proxy server is not running.
- Network firewall rules block the connection port.

#### Remediation Steps

1. Check that the proxy is active:
   ```bash
   curl -I http://localhost:8080/health
   ```
2. Verify host and port configuration in `UNDOLOG_PROXY_URL`.

---

<span id="effect-log-concurrency"></span>
### EFFECT_LOG_CONCURRENCY

**Class:** `EffectLogConcurrencyError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#effect-log-concurrency`
**Description:** A concurrent update to the effect journal caused an optimistic concurrency conflict.

#### Common Causes

- Multiple distributed agent workers updated the same step counter simultaneously.

#### Remediation Steps

1. Retry the operation with exponential backoff.
2. Ensure each parallel agent worker operates within a dedicated session ID.

---

<span id="effect-log-failed"></span>
### EFFECT_LOG_FAILED

**Class:** `EffectLogError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#effect-log-failed`
**Description:** The storage backend failed to persist or retrieve an effect journal entry.

#### Common Causes

- Storage partition is full or read-only.
- Database connection failure.

#### Remediation Steps

1. Check proxy logs for underlying database driver error codes.
2. Confirm persistent volume health and database connectivity.

---

<span id="missing-session"></span>
### MISSING_SESSION

**Class:** `MissingSessionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#missing-session`
**Description:** An instrumented tool was invoked without an active session in scope.

#### Common Causes

- Calling an `@undolog_tool` outside an enclosing `run_with_session()` context.
- Omitting `session_id` in agent graph invocations.

#### Remediation Steps

1. Wrap the tool execution in an `UndoLogSession`:
   ```python
   from undolog_sdk import UndoLogSession, run_with_session

   async with UndoLogSession(org_id="org_demo") as session:
       async with run_with_session(session):
           await my_instrumented_tool()
   ```

---

<span id="not-found"></span>
### NOT_FOUND

**Class:** `NotFoundError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#not-found`
**Description:** A requested resource (such as session, effect, or tool) was not found.

#### Common Causes

- Querying an expired or non-existent session ID.
- Accessing an effect ID that was already purged.

#### Remediation Steps

1. Inspect `exc.resource_type` and `exc.resource_id`.
2. Confirm session IDs before initiating query or resume calls.

---

<span id="permission-denied"></span>
### PERMISSION_DENIED

**Class:** `PermissionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#permission-denied`
**Description:** The authenticated principal lacks permission to perform the requested action.

#### Common Causes

- API key has read-only scope for a write endpoint.
- Organization boundary mismatch.

#### Remediation Steps

1. Check assigned role and scopes for your API key.
2. Re-issue credentials with required permissions.

---

<span id="rate-limited"></span>
### RATE_LIMITED

**Class:** `RateLimitError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#rate-limited`
**Description:** The request exceeded proxy or engine rate limits (HTTP 429).

#### Common Causes

- Request rate exceeds configured requests-per-minute threshold.
- Burst quota depleted during load testing.

#### Remediation Steps

1. Read `exc.retry_after_seconds` or `exc.retry_after_ms`.
2. Wait for the recommended retry interval before resending the request.

---

<span id="serialization-failed"></span>
### SERIALIZATION_FAILED

**Class:** `SerializationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#serialization-failed`
**Description:** Serialization or deserialization of an argument or return value failed.

#### Common Causes

- Non-JSON-serializable objects (such as open file handles or sockets) passed to tools.
- Invalid UTF-8 character sequences.

#### Remediation Steps

1. Inspect tool inputs and outputs for serializable primitives.
2. Convert custom classes into dictionaries or Pydantic models before passing to tools.

---

<span id="server-error"></span>
### SERVER_ERROR

**Class:** `ServerError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#server-error`
**Description:** The UndoLog proxy or upstream service encountered an internal 5xx error.

#### Common Causes

- Proxy unhandled panic or crash.
- Upstream gRPC effect engine unreachable.

#### Remediation Steps

1. Check proxy health endpoints and standard error logs.
2. Verify gRPC connection between proxy and engine services.

---

<span id="timeout"></span>
### TIMEOUT

**Class:** `TimeoutError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#timeout`
**Description:** An operation exceeded its configured deadline.

#### Common Causes

- Slow database queries during heavy effect log indexing.
- Network congestion between client and proxy.

#### Remediation Steps

1. Increase timeout values on `UndoLogClient(timeout=...)`.
2. Inspect `exc.operation` and `exc.timeout` for specific timing details.

---

<span id="tool-not-registered"></span>
### TOOL_NOT_REGISTERED

**Class:** `ToolNotRegisteredError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#tool-not-registered`
**Description:** The tool is not registered in the UndoLog tool registry.

#### Remediation Steps

1. Decorate the tool using `@undolog_tool` or register it via integration wrappers.

---

<span id="tool-registration-failed"></span>
### TOOL_REGISTRATION_FAILED

**Class:** `ToolRegistrationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#tool-registration-failed`
**Description:** Registration of a tool failed due to schema or naming constraints.

#### Remediation Steps

1. Inspect `exc.reason` to adjust schema or tool naming.

---

<span id="invalid-state-transition"></span>
### INVALID_STATE_TRANSITION

**Class:** `InvalidStateTransitionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#invalid-state-transition`
**Description:** The effect cannot transition from its current state to the target state.

#### Remediation Steps

1. Verify effect lifecycle sequence (e.g. do not call `commit()` twice).

---

<span id="approval-not-found"></span>
### APPROVAL_NOT_FOUND

**Class:** `ApprovalNotFoundError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#approval-not-found`
**Description:** The requested approval ID was not found or has already expired.

#### Remediation Steps

1. Refresh the active approvals list before approving or rejecting.

---

<span id="validation-failed"></span>
### VALIDATION_FAILED

**Class:** `ValidationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#validation-failed`
**Description:** Input parameters failed schema or constraint validation.

#### Remediation Steps

1. Inspect `exc.field` and `exc.constraint` to fix invalid arguments.
