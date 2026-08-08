---
title: "Go Proxy REST API Reference"
description: "**Base URL:** `http://<proxy>:8080` **Auth:** `X-Api-Key` header (maps to organisation ID)"
section: "reference"
---
# Go Proxy REST API Reference

**Base URL:** `http://<proxy>:8080`  
**Auth:** `X-Api-Key` header (maps to organisation ID)

---

## `POST /mcp/tool_call`

Intercept and execute a tool call through the UndoLog engine.

### Request

**Headers**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Api-Key` | Yes | API key for tenant authentication. Maps to an organisation ID. |
| `Content-Type` | Yes | `application/json` |

**Body**

```json
{
  "session_id": "uuid",
  "tool_name": "str",
  "tool_version": "str",
  "step_index": 0,
  "args": {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | `string` | Yes | UUID identifying the session. |
| `tool_name` | `string` | Yes | Logical name of the tool. |
| `tool_version` | `string` | No | Semantic version of the tool implementation. |
| `step_index` | `uint32` | No | Monotonically increasing call order within the session. |
| `args` | `object` | Yes | Tool arguments as a JSON object. |

### Responses

#### `200 OK`. Executed

```json
{
  "status": "executed",
  "effect_id": "uuid",
  "result": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"executed"` |
| `effect_id` | `string` | Effect log entry identifier. |
| `result` | `object` | Upstream tool execution result. |

A logical tool failure is returned as `status: "executed"` with
`result.success: false`: when the upstream answers with a `4xx`/`5xx` HTTP
status but a structured `ToolResult` body (as the mock tool server does), the
proxy forwards that result to the engine instead of treating it as a transport
error. Transport failures (unreachable upstream, non-`ToolResult` error bodies,
timeouts) still surface as `502 tool_error`, and the effect is reported as
failed through `Fail`.

`Commit` and `Fail` are retried with bounded backoff on transient engine
failures (for example an unavailable engine), so a momentary engine outage does
not turn into a spurious `502 commit_failed` for an effect that the engine will
record once the connection recovers. Intercept, Approve, and Reject are not
retried. One residual window remains: if the engine applies a `Commit` or
`Fail` but the response is lost in transit, a retried call hits the
already-terminal state and the proxy reports the error even though the effect
is recorded. Execution is still exactly-once there, because the SDK retry
replays the cached result instead of re-running the tool.

#### `200 OK`. Replayed

```json
{
  "status": "replayed",
  "effect_id": "uuid",
  "result": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"replayed"` |
| `effect_id` | `string` | Effect identifier of the original (cached) entry. |
| `result` | `object` | Cached tool result from the original execution. |

#### `202 Accepted`. Pending Approval

```json
{
  "status": "pending_approval",
  "approval_id": "uuid",
  "retry_after": 5
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"pending_approval"` |
| `approval_id` | `string` | Approval request identifier. |
| `retry_after` | `int` | Suggested seconds before retrying the same call. |

#### Error Responses

| Status | `code` | Description |
|--------|--------|-------------|
| `400 Bad Request` | `invalid_request` | Invalid JSON body, missing `session_id`/`tool_name`, or uncanonicalizable args. |
| `401 Unauthorized` | `auth_failed` | Missing `X-Api-Key` header. |
| `403 Forbidden` | `auth_failed` | API key not recognised. |
| `405 Method Not Allowed` | `method_not_allowed` | HTTP method is not `POST`. |
| `502 Bad Gateway` | `intercept_failed` | Engine interception call failed. |
| `502 Bad Gateway` | `tool_error` | Upstream tool execution failed. |
| `502 Bad Gateway` | `commit_failed` | Engine commit call failed after successful execution. |

**Error body:**

```json
{
  "request_id": "uuid",
  "code": "error_code",
  "message": "Human-readable description",
  "timestamp": "2026-01-01T00:00:00Z"
}
```

---

## `GET /health`

Liveness probe for the UndoLog proxy.

### Request

| Header | Required | Description |
|--------|----------|-------------|
|, |, | No auth required. |

### Response

#### `200 OK`

```json
{
  "status": "ok",
  "service": "undolog-proxy",
  "engine_addr": "localhost:50051",
  "upstream_url": "http://upstream-tool-server"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"ok"` |
| `service` | `string` | Service identifier. |
| `engine_addr` | `string` | Engine gRPC endpoint (from config). |
| `upstream_url` | `string` | Upstream tool execution URL (from config). |

---

## `GET /approvals`

List approval requests filtered by organisation and state.

### Request

**Headers**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Api-Key` | Yes | API key for tenant authentication. |
| `X-Org-Id` | Yes | Organisation identifier (set by auth middleware). |

**Query Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `state` | `string` | No | `pending` | Filter by approval state. One of: `pending`, `approved`, `rejected`. |
| `limit` | `int` | No | `100` | Maximum number of records to return (1-500). Records are ordered newest first, with the ID as tiebreaker. |

### Response

#### `200 OK`

```json
[
  {
    "id": "uuid",
    "org_id": "uuid",
    "session_id": "uuid",
    "effect_id": "uuid",
    "tool_name": "transfer_funds",
    "args": [123, 34, ...],
    "status": "pending"
  }
]
```

Each record contains:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Approval request identifier. |
| `org_id` | `string` | Organisation identifier. |
| `session_id` | `string` | Session identifier. |
| `effect_id` | `string` | Effect identifier. |
| `tool_name` | `string` | Tool name. |
| `args` | `bytes` | Raw JSON tool arguments. |
| `status` | `string` | One of: `pending`, `approved`, `rejected`. |

#### Error Responses

| Status | Description |
|--------|-------------|
| `400 Bad Request` | Invalid `state` parameter. An invalid or out-of-range `limit` falls back to the default (100). |
| `401 Unauthorized` | Missing `X-Org-Id` header. |

---

## `POST /approvals/{id}/approve`

Approve a pending approval request and resume the suspended session.

### Request

**Headers**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Api-Key` | Yes | API key for tenant authentication. |
| `X-Org-Id` | Yes | Organisation identifier (set by auth middleware). |

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | `string` | Approval request identifier. |

### Response

#### `200 OK`

```json
{
  "status": "approved",
  "approval_id": "uuid"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"approved"` |
| `approval_id` | `string` | Approval request identifier. |

#### Error Responses

| Status | Description |
|--------|-------------|
| `400 Bad Request` | Missing approval ID in path or malformed decision body. |
| `401 Unauthorized` | Missing `X-Org-Id` header. |
| `404 Not Found` | Approval ID not found or does not belong to the organisation. |
| `409 Conflict` | Approval already resolved. |
| `502 Bad Gateway` | Engine rejected the approval decision. |

---

## `POST /approvals/{id}/reject`

Reject a pending approval request and halt the suspended session.

### Request

**Headers**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Api-Key` | Yes | API key for tenant authentication. |
| `X-Org-Id` | Yes | Organisation identifier (set by auth middleware). |

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | `string` | Approval request identifier. |

### Response

#### `200 OK`

```json
{
  "status": "rejected",
  "approval_id": "uuid"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"rejected"` |
| `approval_id` | `string` | Approval request identifier. |

#### Error Responses

| Status | Description |
|--------|-------------|
| `400 Bad Request` | Missing approval ID in path or malformed decision body. |
| `401 Unauthorized` | Missing `X-Org-Id` header. |
| `404 Not Found` | Approval ID not found or does not belong to the organisation. |
| `409 Conflict` | Approval already resolved. |
| `502 Bad Gateway` | Engine rejected the rejection decision. |

---

## `GET /events`

Server-Sent Events stream for real-time dashboard updates.

### Request

**Headers**

| Header | Required | Description |
|--------|----------|-------------|
| `X-Api-Key` | Yes | API key for tenant authentication. |
| `X-Org-Id` | Yes | Organisation identifier (set by auth middleware). |

### Response

`text/event-stream` with `Cache-Control: no-cache`.

**Event types:**

| Event | Description |
|-------|-------------|
| `effect_intercepted` | Tool call reached the engine for interception. |
| `effect_executed` | Tool call ran upstream. |
| `effect_committed` | Engine committed the effect successfully. |
| `effect_replayed` | Tool call served from cached state. |
| `effect_failed` | Failure at any interception stage. |
| `approval_required` | Tool call waiting for human approval. |
| `approval_approved` | Human approved an approval request. |
| `approval_rejected` | Human rejected an approval request. |

**Event format:**

```
event: effect_committed
id: 1704067200000000000
data: {"type":"effect_committed","timestamp":"2026-01-01T00:00:00Z","org_id":"...","session_id":"...","effect_id":"...","payload":{"stage":"committed"}}

```

Heartbeat (`: ping`) sent every 25 seconds to maintain connection.

### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing org ID via header or query parameter. |
| `500 Internal Server Error` | Streaming unsupported by proxy. |
