---
title: "Rust Engine gRPC API Reference"
description: "**Package:** `undolog.v1` **Service:** `UndoLogEngine` **Address:** `:50051` **Proto:** `proto/undolog.proto` **Server:** Rust (`tonic`), **Client:** Go (`grpc`)"
section: "reference"
---
# Rust Engine gRPC API Reference

**Package:** `undolog.v1`  
**Service:** `UndoLogEngine`  
**Address:** `:50051`  
**Proto:** `proto/undolog.proto`  
**Server:** Rust (`tonic`), **Client:** Go (`grpc`)

---

## Authentication

The engine supports optional bearer token authentication on its gRPC interface.

Enforcement is configured by setting the `UNDOLOG_ENGINE_AUTH_TOKEN` environment
variable on the engine process. When set, callers must supply the token on every
RPC via request metadata. The Go MCP proxy sends this token when configured with
`UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` (see ADR 0018:
`docs/adr/0018-proxy-grpc-auth-method.md`).

### Request Metadata

Every incoming RPC must include the bearer token in the gRPC metadata when
authentication is active:

| Metadata key | Value format | Description |
|--------------|--------------|-------------|
| `authorization` | `Bearer <token>` | Lowercase ASCII header with the literal prefix `Bearer ` followed by the shared secret token. |

### Verification Semantics

- **Authentication disabled:** If `UNDOLOG_ENGINE_AUTH_TOKEN` is unset, empty,
  or whitespace only, the engine interceptor permits all RPC requests without
  checking metadata. This is the default for local development.
- **Authentication enabled:** If `UNDOLOG_ENGINE_AUTH_TOKEN` is set, every
  incoming RPC passes through the token interceptor. The presented token is
  extracted from `authorization` metadata and validated using constant-time
  comparison (`constant_time_eq`) to prevent timing side channels.
- **Rejection behavior:** Missing credentials, non-ASCII metadata values,
  missing `Bearer ` prefixes, or mismatched tokens are rejected before handler
  execution with gRPC status `UNAUTHENTICATED` (code 16) and message:
  `missing or invalid credentials: expected \`authorization\` metadata as \`Bearer <token>\``.
  The rejection message never echoes configured or presented tokens.

---

## Service Definition

```protobuf
service UndoLogEngine {
  rpc Intercept(InterceptRequest) returns (InterceptResponse);
  rpc Commit(CommitRequest) returns (CommitResponse);
  rpc Fail(FailRequest) returns (FailResponse);
 rpc Approve(ApproveRequest) returns (ApproveResponse);
 rpc Reject(RejectRequest) returns (RejectResponse);
 rpc ListPendingApprovals(ListPendingApprovalsRequest) returns (ListPendingApprovalsResponse);
}
```

---

## Shared Types

### `ToolCall`

Canonical tool invocation exchanged between proxy and engine.

| Field | Type | Description |
|-------|------|-------------|
| `org_id` | `string` | Organisation that owns this call. Scopes all subsequent lookups. |
| `session_id` | `string` | Session that produced the call (UUID v4). |
| `tool_id` | `string` | Registered tool identifier, if known. |
| `tool_name` | `string` | Logical name of the tool (e.g. `"transfer_funds"`). |
| `tool_version` | `string` | Semantic version of the tool implementation. |
| `step_index` | `uint32` | Monotonically increasing call order within the session. |
| `args` | `bytes` | Raw JSON arguments supplied by the caller. |
| `signature` | `string` | Canonical BLAKE3 deduplication key (64 lowercase hex chars). |

### `ToolResult`

Represents the upstream tool execution result.

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether the execution completed without error. |
| `output` | `bytes` | Raw JSON payload returned by the tool (omitted on error). |
| `error` | `string` | Human-readable error description (present only on failure). |
| `duration_ms` | `uint64` | Wall-clock duration of the upstream execution in milliseconds. |

---

## RPC: `Intercept`

Intercept a tool call before execution. The engine decides whether to Execute it fresh, return a cached result (Replay), or wait for human approval (AwaitingApproval).

### `InterceptRequest`

| Field | Type | Description |
|-------|------|-------------|
| `tool_call` | `ToolCall` | The intercepted tool call. |

### `InterceptResponse`

| Field | Type | Description |
|-------|------|-------------|
| `outcome` | `oneof(ExecuteOutcome, ReplayOutcome, AwaitingApprovalOutcome)` | Routing decision from the engine. |
| `error` | `string` | Engine-side error description. Populated only when the engine could not process the request (not a tool execution error). |

#### `outcome` oneof variants

**`execute` (ExecuteOutcome)**

| Field | Type | Description |
|-------|------|-------------|
| `effect_id` | `string` | Unique effect identifier for the newly created log entry. |

**`replay` (ReplayOutcome)**

| Field | Type | Description |
|-------|------|-------------|
| `effect_id` | `string` | Effect identifier of the original (cached) entry. |
| `cached_result` | `ToolResult` | The cached tool result from the original execution. |

**`awaiting_approval` (AwaitingApprovalOutcome)**

| Field | Type | Description |
|-------|------|-------------|
| `effect_id` | `string` | Effect identifier for the pending entry. |
| `approval_id` | `string` | Approval request identifier that the caller should surface. |

---

## RPC: `Commit`

Commit reports a successful tool execution so the engine can persist the result snapshot and transition the effect to Committed.

### `CommitRequest`

| Field | Type | Description |
|-------|------|-------------|
| `effect_id` | `string` | Effect identifier from the InterceptResponse (Execute outcome). |
| `org_id` | `string` | Organisation that owns the effect. |
| `session_id` | `string` | Session that produced the call. |
| `result` | `ToolResult` | The successful tool execution result. |

### `CommitResponse`

Empty message.

---

## RPC: `Fail`

Fail reports a failed tool execution so the engine can record the error and revert the effect to a failure state.

### `FailRequest`

| Field | Type | Description |
|-------|------|-------------|
| `effect_id` | `string` | Effect identifier from the InterceptResponse. |
| `org_id` | `string` | Organisation that owns the effect. |
| `session_id` | `string` | Session that produced the call. |
| `error` | `string` | Human-readable error description. |

### `FailResponse`

Empty message.

---

## RPC: `Approve`

Approve resolves an AwaitingApproval effect positively. The engine transitions the session back to Active so execution can resume.

### `ApproveRequest`

| Field | Type | Description |
|-------|------|-------------|
| `approval_id` | `string` | Approval request identifier from the InterceptResponse (AwaitingApproval). |
| `org_id` | `string` | Organisation that owns the approval request. |
| `actor` | `string` | Identity of the human who granted approval (for audit). |
| `approved_args` | `bytes` | Optional modified args to use in place of the original proposed args. |

### `ApproveResponse`

Carries the resume parameters for the approved tool call so the caller can
proceed with execution.

| Field | Type | Description |
|-------|------|-------------|
| `effect_id` | `string` | Effect identifier that transitioned from pending to approved. |
| `session_id` | `string` | Session that was awaiting approval, now resumed to active. |
| `tool_name` | `string` | Logical name of the tool to execute. |
| `tool_version` | `string` | Semantic version of the tool implementation. |
| `args` | `bytes` | Raw JSON arguments to execute with: contains `approved_args` if overridden by the approver, or the original proposed args. |

---

## RPC: `Reject`

Reject resolves an AwaitingApproval effect negatively. The engine transitions the session to Halted and prevents further execution.

### `RejectRequest`

| Field | Type | Description |
|-------|------|-------------|
| `approval_id` | `string` | Approval request identifier from the InterceptResponse (AwaitingApproval). |
| `org_id` | `string` | Organisation that owns the approval request. |
| `actor` | `string` | Identity of the human who rejected the request (for audit). |

### `RejectResponse`

Empty message.

## RPC: `ListPendingApprovals`

Lists approval requests still awaiting a decision, ordered oldest first. Used
by the proxy to reconcile pending approvals after a restart or network
disconnect, so in-flight requests are not orphaned.

### `ListPendingApprovalsRequest`

| Field | Type | Description |
|-------|------|-------------|
| `org_id` | `string` | Organisation whose pending approval requests are listed. |

### `ListPendingApprovalsResponse`

| Field | Type | Description |
|-------|------|-------------|
| `records` | `repeated ApprovalRecord` | Pending approval requests, oldest first. |

### `ApprovalRecord`

| Field | Type | Description |
|-------|------|-------------|
| `approval_id` | `string` | Approval request identifier. |
| `org_id` | `string` | Organisation that owns the approval request. |
| `session_id` | `string` | Session that requested the approval. |
| `effect_id` | `string` | Effect awaiting the decision. |
| `tool_name` | `string` | Name of the tool proposed for execution. |
| `args_json` | `string` | JSON-serialised arguments proposed for the tool. |
| `created_at_unix_ms` | `int64` | Creation time in Unix milliseconds. |

---

## Dead-Letter Queue Operations

The engine manages compensations that fail permanently and exhaust retry limits
in a dedicated dead-letter store (`undolog_dead_letters`). Dead letters allow
operators and automated tools to inspect, retry, or skip blocked compensations.

### Lifecycle States

| State | Classification | Description | Allowed next transitions |
|-------|----------------|-------------|--------------------------|
| `failed` | Initial | Compensation exhausted its retry budget (default: 3 attempts). Awaiting operator action. | `retry` -> `retrying`, `skip` -> `skipped` |
| `retrying` | Transient | A retry has been initiated and is actively being executed. Increments `retry_count`. | `failed` (if retry fails again) or resolved |
| `skipped` | Terminal | The compensation was intentionally skipped by an operator. No further execution occurs. | None (terminal state) |

### State Transitions

- **`failed` -> `retrying`:** Triggered by the retry operation. Increments `retry_count` by 1.
- **`failed` -> `skipped`:** Triggered by the skip operation. Marks the dead-letter resolved without compensation re-execution.
- **Invalid transitions:** Calling retry or skip on a record that is not in `failed` state returns an error.

### Operations

#### `ListDeadLetters`

List dead-letter records for an organization, optionally filtered by state.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `org_id` | `string` (UUID) | Yes | Organisation that owns the dead letters. |
| `state` | `string` | No | State filter (`"failed"`, `"retrying"`, `"skipped"`). Defaults to `"failed"`. |

**Response:**

| Field | Type | Description |
|-------|------|-------------|
| `records` | `repeated DeadLetterRecord` | List of matching dead-letter records. |
| `count` | `uint64` | Total number of records returned. |

---

#### `RetryDeadLetter`

Reset a dead letter from `failed` to `retrying` and increment its retry counter,
enabling re-execution of the failed compensation.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `dead_letter_id` | `string` (UUID) | Yes | Unique identifier of the dead-letter record to retry. |

**Response:**

Returns the updated `DeadLetterRecord` in `retrying` state with `retry_count` incremented by 1.

**Errors:**

- Returns 404 Not Found when the dead letter ID does not exist or its current state is not `failed`.

---

#### `SkipDeadLetter`

Mark a dead-letter record as `skipped` to resolve it permanently without
re-running compensation.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `dead_letter_id` | `string` (UUID) | Yes | Unique identifier of the dead-letter record to skip. |

**Response:**

Returns the updated `DeadLetterRecord` in `skipped` state.

**Errors:**

- Returns 404 Not Found when the dead letter ID does not exist or its current state is not `failed`.

---

### `DeadLetterRecord` Schema

| Field | Type | Description |
|-------|------|-------------|
| `dead_letter_id` | `string` (UUID) | Unique identifier for the dead-letter record. |
| `org_id` | `string` (UUID) | Organisation that owns the record. |
| `session_id` | `string` (UUID) | Session that produced the failed compensation. |
| `effect_id` | `string` (UUID) | Effect whose compensation failed. |
| `undo_id` | `string` (UUID) | Undo entry that failed. |
| `compensation_fn` | `string` | Logical name of the compensation function. |
| `compensation_version` | `string` | Semantic version of the compensation function. |
| `compensation_args` | `bytes` / `string` | JSON-encoded arguments supplied to the compensation function. |
| `error_message` | `string` | Error message captured from the failed compensation attempts. |
| `retry_count` | `int32` | Number of retry attempts executed so far. |
| `state` | `string` | Lifecycle state (`"failed"`, `"retrying"`, or `"skipped"`). |
| `created_at` | `string` | Creation timestamp in RFC 3339 format. |
| `updated_at` | `string` | Last modification timestamp in RFC 3339 format. |
