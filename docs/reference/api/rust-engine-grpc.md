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

## Service Definition

```protobuf
service UndoLogEngine {
  rpc Intercept(InterceptRequest) returns (InterceptResponse);
  rpc Commit(CommitRequest) returns (CommitResponse);
  rpc Fail(FailRequest) returns (FailResponse);
  rpc Approve(ApproveRequest) returns (ApproveResponse);
  rpc Reject(RejectRequest) returns (RejectResponse);
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

Empty message.

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
