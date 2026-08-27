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

Every response carries an `X-Request-Id` header (and the error body includes the
same value as `request_id`). The proxy forwards this value as `x-request-id`
gRPC metadata on every engine call, so engine and proxy logs for one tool call
can be correlated across the two services.

---

## `GET /health`

Liveness probe for the UndoLog proxy.

### Request

No headers are required and the endpoint is unauthenticated.

### Response

#### `200 OK`

```json
{
  "status": "ok",
  "service": "undolog-proxy"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"ok"` |
| `service` | `string` | Service identifier. |

The endpoint is a liveness probe only: it does not check the engine connection
or upstream reachability, and it does not echo configuration values (such as
the engine address or upstream URL), which are kept out of unauthenticated
responses.

---

## `GET /metrics`

Prometheus text exposition of proxy service metrics. No auth required.

### Metric families

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `undolog_proxy_http_requests_total` | counter | `route`, `status` | Completed HTTP requests by route and status code. |
| `undolog_proxy_http_request_duration_seconds` | histogram | `route` | HTTP request duration per route. Note that `/events` reports the full stream lifetime. |
| `undolog_proxy_engine_rpc_duration_seconds` | histogram | `method` | Engine gRPC call duration. |
| `undolog_proxy_engine_rpc_errors_total` | counter | `method` | Engine calls that returned an error. |
| `undolog_proxy_engine_rpc_retries_total` | counter | `method` | Engine calls retried after a transient failure. |
| `undolog_proxy_executor_duration_seconds` | histogram | `result` | Upstream tool executor duration. |
| `undolog_proxy_sse_subscribers` | gauge | `org` | Active SSE subscribers per organisation. |
| `undolog_proxy_sse_events_dropped_total` | counter | `org` | SSE events dropped because a subscriber channel was full. |
| `undolog_proxy_approval_decisions_total` | counter | `action`, `result` | Approval decisions by action and outcome. |
| `undolog_proxy_approval_decision_duration_seconds` | histogram | `action` | Approval decision latency. |

The metric endpoint is unauthenticated and intentionally carries no
configuration or API-key material. If it must not be reachable by the public,
scope it behind a reverse-proxy rule.

The `route` label carries the request path with the approval id collapsed to a
fixed segment, so `/approvals/{id}/approve` and `/approvals/{id}/reject`
produce one series per action instead of one per approval id. The `/events`
duration histogram covers the full SSE stream lifetime. Approval latency only
samples requests that reached the decision state machine. `/health` and
`/metrics` are served outside the middleware chain, so they do not appear in
the HTTP metrics and are not covered by API-key auth.

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
  "approval_id": "uuid",
  "effect_id": "uuid",
  "execution": "committed",
  "result": {}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `"approved"` |
| `approval_id` | `string` | Approval request identifier. |
| `effect_id` | `string` | Effect identifier. |
| `execution` | `string` | `"committed"` on success, `"failed"` if the tool execution failed after approval. |
| `result` | `object` | Upstream tool execution result (present when `execution` is `"committed"`). |
| `error` | `string` | Execution error message (present when `execution` is `"failed"`). |

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
