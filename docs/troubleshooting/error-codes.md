---
title: "SDK Error Codes and Troubleshooting Runbooks"
description: "Reference guide and actionable remediation runbooks for UndoLog SDK errors."
section: "troubleshooting"
---

# SDK Error Codes and Troubleshooting Runbooks

Every error thrown by the UndoLog SDK inherits from `UndoLogError`. Each instance provides:

1. `code`: A machine-readable identifier for programmatic error handling.
2. `message`: A descriptive human-readable failure summary.
3. `docsUrl`: A direct link to the corresponding troubleshooting runbook on this page.
4. `timestamp`: The Unix timestamp (epoch milliseconds) when the error occurred.

---

## Quick Reference Table

| Error Code | Error Class | Retryable | Runbook Anchor |
|------------|-------------|-----------|----------------|
| `AUTHENTICATION_FAILED` | `AuthenticationError` | No | [#authentication-failed](#authentication-failed) |
| `AWAITING_APPROVAL` | `AwaitingApprovalError` | No | [#awaiting-approval](#awaiting-approval) |
| `COMPENSATION_FAILED` | `CompensationError` | Yes | [#compensation-failed](#compensation-failed) |
| `CONFIGURATION_INVALID` | `ConfigurationError` | No | [#configuration-invalid](#configuration-invalid) |
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

## Error Runbooks

<span id="authentication-failed"></span>
### AUTHENTICATION_FAILED

**Class:** `AuthenticationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#authentication-failed`
**Description:** The API key or authentication token provided to the UndoLog proxy is missing, expired, or invalid.

#### Common Causes

- `UNDOLOG_API_KEY` environment variable is unset or empty.
- The supplied bearer token has expired.
- The key belongs to a different organisation or environment.

#### Remediation Steps

1. Verify that `UNDOLOG_API_KEY` is present in your environment:
   ```bash
   echo "$UNDOLOG_API_KEY"
   ```
2. Check your client configuration when initializing `UndoLogClient`:
   ```typescript
   import { UndoLogClient } from "@undolog/sdk";

   const client = new UndoLogClient({
     apiKey: process.env.UNDOLOG_API_KEY!,
     baseUrl: process.env.UNDOLOG_PROXY_URL || "http://localhost:8080",
   });
   ```
3. Generate a new API key from the UndoLog dashboard if the current token has expired.

---

<span id="awaiting-approval"></span>
### AWAITING_APPROVAL

**Class:** `AwaitingApprovalError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#awaiting-approval`
**Description:** The invoked tool is classified under the `Irreversible` tier and requires human approval before execution can proceed.

#### Common Causes

- An agent called an irreversible tool (for example: database migration, financial transfer, customer email broadcast).
- The execution context has no pre-approved grant for this tool and payload signature.

#### Remediation Steps

1. Catch `AwaitingApprovalError` in your agent loop to extract the `approvalId` and `toolName`:
   ```typescript
   import { AwaitingApprovalError } from "@undolog/sdk";

   try {
     await agent.run(task);
   } catch (error) {
     if (error instanceof AwaitingApprovalError) {
       console.log(`Tool ${error.toolName} requires human approval.`);
       console.log(`Approval ID: ${error.approvalId}`);
       console.log(`Payload arguments:`, error.args);
     }
   }
   ```
2. Review the pending approval request in the dashboard or approve via the API.
3. Once approved, re-invoke the step: UndoLog will detect the approval grant and proceed with execution.

---

<span id="compensation-failed"></span>
### COMPENSATION_FAILED

**Class:** `CompensationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#compensation-failed`
**Description:** A compensating action failed during a saga rollback after exhausting all configured retry attempts.

#### Common Causes

- The downstream external service or API used for compensation is unreachable or returning 5xx errors.
- Inverse arguments provided during tool execution were invalid or expired.
- Network partition occurred while executing the rollback handler.

#### Remediation Steps

1. Inspect `error.fnName` and `error.cause` to diagnose the failure:
   ```typescript
   import { CompensationError } from "@undolog/sdk";

   try {
     await saga.rollback();
   } catch (error) {
     if (error instanceof CompensationError) {
       console.error(`Compensation failed for function: ${error.fnName}`);
       console.error(`Root cause:`, error.cause);
     }
   }
   ```
2. Verify downstream service health and connectivity.
3. Review the saga audit log in the effect engine to inspect pending compensations and perform manual reconciliation if necessary.

---

<span id="configuration-invalid"></span>
### CONFIGURATION_INVALID

**Class:** `ConfigurationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#configuration-invalid`
**Description:** The SDK was initialized with invalid, conflicting, or missing configuration options.

#### Common Causes

- Missing required fields such as `baseUrl` or `apiKey`.
- Malformed URLs (for example: missing scheme or invalid port).
- Incompatible timeout or retry parameters (for example: negative retry counts or invalid intervals).

#### Remediation Steps

1. Check the specific option identified by `error.key`:
   ```typescript
   import { ConfigurationError } from "@undolog/sdk";

   try {
     new UndoLogClient({ baseUrl: "" });
   } catch (error) {
     if (error instanceof ConfigurationError) {
       console.error(`Invalid option: ${error.key}. Details: ${error.message}`);
     }
   }
   ```
2. Ensure `baseUrl` is a valid HTTP or HTTPS endpoint.
3. Set sensible defaults for timeouts (typically 5000ms to 30000ms).

---

<span id="effect-log-concurrency"></span>
### EFFECT_LOG_CONCURRENCY

**Class:** `EffectLogConcurrencyError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#effect-log-concurrency`
**Description:** Optimistic concurrency control detected a conflicting version update on the effect journal entry.

#### Common Causes

- Multiple concurrent workers or agent instances attempted to update or commit the same effect simultaneously.
- A delayed network response replayed an outdated effect state version.

#### Remediation Steps

1. Inspect `expectedVersion` and `actualVersion` from the error instance:
   ```typescript
   import { EffectLogConcurrencyError } from "@undolog/sdk";

   try {
     await effectLog.commit(effectId, version);
   } catch (error) {
     if (error instanceof EffectLogConcurrencyError) {
       console.warn(`Version conflict: expected ${error.expectedVersion}, found ${error.actualVersion}`);
     }
   }
   ```
2. Fetch the latest effect state from the journal and re-evaluate whether execution or replay is required.
3. Ensure separate session identifiers are assigned to parallel agent execution paths.

---

<span id="effect-log-failed"></span>
### EFFECT_LOG_FAILED

**Class:** `EffectLogError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#effect-log-failed`
**Description:** A persistence operation (write, read, or flush) on the effect log storage backend failed.

#### Common Causes

- Storage backend or database connection pool is exhausted.
- Storage disk volume is full or operating in read-only mode.
- Temporary network glitch between SDK proxy and database backend.

#### Remediation Steps

1. Check `error.operation` to see whether a read, write, or flush failed.
2. Verify database connectivity and connection pool metrics.
3. For transient database errors, retry the operation with exponential backoff.

---

<span id="missing-session"></span>
### MISSING_SESSION

**Class:** `MissingSessionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#missing-session`
**Description:** A tool decorated with UndoLog safety controls executed without an active session context.

#### Common Causes

- A decorated tool was called directly in application code without entering `runWithSession()`.
- An asynchronous callback or detached promise lost AsyncLocalStorage context.

#### Remediation Steps

1. Wrap the tool invocation inside `runWithSession()`:
   ```typescript
   import { runWithSession } from "@undolog/sdk";

   await runWithSession(
     { sessionId: "sess_123", orgId: "org_456" },
     async () => {
       await myDecoratedTool({ query: "example" });
     }
   );
   ```
2. If executing tools outside ambient context, provide an explicit `sessionId` parameter where supported.

---

<span id="not-found"></span>
### NOT_FOUND

**Class:** `NotFoundError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#not-found`
**Description:** The requested resource (effect, session, or tool registration) does not exist in storage.

#### Common Causes

- Querying an effect ID that was never registered or was pruned by retention policies.
- Referencing a session that has already completed and been archived.

#### Remediation Steps

1. Inspect `error.resourceType` and `error.resourceId`:
   ```typescript
   import { NotFoundError } from "@undolog/sdk";

   try {
     const effect = await client.getEffect(effectId);
   } catch (error) {
     if (error instanceof NotFoundError) {
       console.error(`Resource missing: ${error.resourceType} (${error.resourceId})`);
     }
   }
   ```
2. Verify the ID was created within the active organisation scope.

---

<span id="permission-denied"></span>
### PERMISSION_DENIED

**Class:** `PermissionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#permission-denied`
**Description:** The authenticated principal does not have permission to execute the requested operation.

#### Common Causes

- API key role is restricted to read-only access.
- Cross-tenant access attempt: the requested session or effect belongs to another organisation.

#### Remediation Steps

1. Check `error.permission` for the required privilege.
2. Verify that the configured `orgId` matches the resource's owner.
3. Grant the required permissions in your API key management console.

---

<span id="rate-limited"></span>
### RATE_LIMITED

**Class:** `RateLimitError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#rate-limited`
**Description:** Request rate limit exceeded for the client or organisation.

#### Common Causes

- Burst of rapid tool executions exceeding quota limits.
- Excessive parallel agent loops targeting the proxy simultaneously.

#### Remediation Steps

1. Read `error.retryAfterSeconds` to know when to retry:
   ```typescript
   import { RateLimitError } from "@undolog/sdk";

   try {
     await client.intercept(callParams);
   } catch (error) {
     if (error instanceof RateLimitError) {
       const delayMs = (error.retryAfterSeconds ?? 5) * 1000;
       await new Promise((resolve) => setTimeout(resolve, delayMs));
       return client.intercept(callParams);
     }
   }
   ```
2. Implement client-side rate limiting or increase your tier quota in dashboard settings.

---

<span id="serialization-failed"></span>
### SERIALIZATION_FAILED

**Class:** `SerializationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#serialization-failed`
**Description:** Tool arguments or return values could not be serialized to canonical JSON format.

#### Common Causes

- Circular object references in tool input or output payloads.
- Unsupported data types such as functions, symbols, or non-finite numbers (`NaN`, `Infinity`).

#### Remediation Steps

1. Sanitize tool arguments before passing them into the tool execution:
   ```typescript
   function sanitize(obj: unknown): unknown {
     return JSON.parse(JSON.stringify(obj));
   }
   ```
2. Ensure all numbers are finite. Use string or BigInt representations for values exceeding IEEE-754 precision.

---

<span id="server-error"></span>
### SERVER_ERROR

**Class:** `ServerError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#server-error`
**Description:** The UndoLog proxy or upstream engine returned an unexpected HTTP 5xx response.

#### Common Causes

- Internal server error in the proxy or gRPC engine service.
- Infrastructure gateway timeout or reverse proxy failure.

#### Remediation Steps

1. Check `error.statusCode` (for example: 500, 502, 503, 504).
2. For 502/503/504 errors, retry the request with exponential backoff.
3. Check status page and engine logs if 500 errors persist.

---

<span id="timeout"></span>
### TIMEOUT

**Class:** `TimeoutError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#timeout`
**Description:** An operation exceeded its configured deadline before completing.

#### Common Causes

- Downstream tool execution took longer than `timeoutMs`.
- Slow database query or proxy network latency.

#### Remediation Steps

1. Inspect `error.operation` and `error.timeoutMs`.
2. Increase timeout configuration for long-running tools:
   ```typescript
   const client = new UndoLogClient({
     timeoutMs: 15000,
   });
   ```
3. Optimize downstream tool performance or split large batch tasks into smaller steps.

---

<span id="tool-not-registered"></span>
### TOOL_NOT_REGISTERED

**Class:** `ToolNotRegisteredError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#tool-not-registered`
**Description:** An agent attempted to invoke a tool that has not been registered in the effect engine registry.

#### Common Causes

- Tool name typo in agent prompt or tool definition.
- Tool was not registered before executing the agent loop.

#### Remediation Steps

1. Verify tool registration on the client before execution:
   ```typescript
   await client.registerTool({
     name: "calculateTax",
     tier: ToolTier.Safe,
     description: "Calculates sales tax",
   });
   ```
2. Confirm the tool name passed to the agent matches the registered name exactly.

---

<span id="tool-registration-failed"></span>
### TOOL_REGISTRATION_FAILED

**Class:** `ToolRegistrationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#tool-registration-failed`
**Description:** Failed to register a tool in the effect engine registry.

#### Common Causes

- Tool name conflicts with an existing tool having a different tier.
- Malformed schema or invalid tier parameter.

#### Remediation Steps

1. Check `error.toolName` and `error.reason`.
2. Ensure tool names are unique within your organisation or register idempotent updates with matching tier definitions.

---

<span id="invalid-state-transition"></span>
### INVALID_STATE_TRANSITION

**Class:** `InvalidStateTransitionError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#invalid-state-transition`
**Description:** Attempted an effect state change that violates the UndoLog state machine lifecycle.

#### Common Causes

- Calling `commit()` on an effect that is already committed or failed.
- Calling `commit()` directly on a replayed effect without executing.

#### Remediation Steps

1. Inspect `error.currentState` and `error.targetState`.
2. Follow the standard effect lifecycle:
   ```
   Pending -> Executing -> Committed | Failed
   ```
3. Do not re-commit an effect if `intercept()` returned an existing replay outcome.

---

<span id="approval-not-found"></span>
### APPROVAL_NOT_FOUND

**Class:** `ApprovalNotFoundError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#approval-not-found`
**Description:** The specified approval request ID does not exist or has already been resolved.

#### Common Causes

- Using an outdated or invalid `approvalId`.
- The approval was already approved or rejected by another operator.

#### Remediation Steps

1. Inspect `error.approvalId`.
2. Refresh the active approvals list from the dashboard or API before submitting approval decisions.

---

<span id="validation-failed"></span>
### VALIDATION_FAILED

**Class:** `ValidationError`
**Runbook URL:** `https://undolog.dev/docs/troubleshooting/errors#validation-failed`
**Description:** Input parameters or arguments failed schema validation constraints.

#### Common Causes

- Missing required arguments in tool calls.
- Type mismatch (for example: string passed when number expected).
- Constraint violation (for example: negative value when positive required).

#### Remediation Steps

1. Inspect `error.field` and `error.constraint`:
   ```typescript
   import { ValidationError } from "@undolog/sdk";

   try {
     validateArgs(args);
   } catch (error) {
     if (error instanceof ValidationError) {
       console.error(`Validation failed on field "${error.field}": ${error.constraint}`);
     }
   }
   ```
2. Verify argument payloads against your tool schema before invocation.
