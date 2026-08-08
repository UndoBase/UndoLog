---
title: "Configuration Reference"
description: "All configuration is through environment variables. Organised by service."
section: "reference"
---
# Configuration Reference

All configuration is through environment variables. Organised by service.

---

## Rust Engine

### `DATABASE_URL`

| Type | Default | Required |
|------|---------|----------|
| `string` | `postgresql://postgres:postgres@localhost:5432/undolog_dev` | Yes |

PostgreSQL connection string for effect storage, sessions, approvals, and tool registry.
Format: `postgresql://[user[:password]@][host][:port][/database][?params]`

---

### `UNDOLOG_ENGINE_GRPC_ADDR`

| Type | Default | Required |
|------|---------|----------|
| `string` | `0.0.0.0:50051` | Yes |

IP:port for the tonic gRPC server that handles Intercept, Commit, Fail, Approve, Reject RPCs.

---

### `UNDOLOG_ENGINE_HEALTH_ADDR`

| Type | Default | Required |
|------|---------|----------|
| `string` | `0.0.0.0:9090` | Yes |

IP:port for the HTTP health endpoint (`GET /` → `{"status":"ok","service":"undolog-engine"}`).

---

### `UNDOLOG_LOG_LEVEL`

| Type | Default | Valid Values |
|------|---------|-------------|
| `string` | `info` | `trace`, `debug`, `info`, `warn`, `error` |

Log level for the Rust engine. Used as the default directive for `tracing_subscriber::EnvFilter`.

---

### `UNDOLOG_REGISTRY_REFRESH_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `uint64` | `60` | Interval in seconds between tool registry refreshes from the database. The engine loads `undolog_tool_registry` into an in-memory cache on startup and refreshes periodically at this interval. |

---

### `UNDOLOG_LOCK_MAX_ATTEMPTS`

| Type | Default | Description |
|------|---------|-------------|
| `uint32` | `3` | Maximum number of attempts to acquire a PostgreSQL advisory lock for a call signature before returning `AdvisoryLockTimeout`. |

---

### `UNDOLOG_LOCK_RETRY_MS`

| Type | Default | Description |
|------|---------|-------------|
| `uint64` | `100` | Delay in milliseconds between advisory lock acquisition attempts. |

---

## Go Proxy

### `UNDOLOG_PROXY_LISTEN_ADDR`

| Type | Default | Required |
|------|---------|----------|
| `string` | `:8080` | Yes |

HTTP bind address for the MCP proxy server.

---

### `UNDOLOG_PROXY_READ_TIMEOUT_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `15` | Maximum seconds the server waits for request headers. |

---

### `UNDOLOG_PROXY_WRITE_TIMEOUT_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `15` | Maximum seconds the server waits while writing responses. |

---

### `UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `30` | Maximum seconds for graceful shutdown before force closing connections. |

---

### `UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `30` | Maximum seconds for tool execution and engine RPC calls. Applied as a context timeout on the interception flow and as the upstream tool executor client timeout. |

---

### `UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `3` | Maximum number of attempts for the engine connection bootstrap and for transient Commit/Fail RPC failures. Deterministic failures (for example an invalid state transition) are never retried. Setting this to `1` disables Commit/Fail retries. |

---

### `UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `100` | Base delay in milliseconds between engine connection and Commit/Fail retry attempts, scaled linearly per attempt. |

---

### `UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `128` | Per-organisation SSE channel buffer size for dashboard event delivery. Events beyond this size are dropped (non-blocking). |

---

### `UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `60` | Seconds between reconciliations of pending approvals from the engine database. Reconciliation also runs once on startup. |

---

### `UNDOLOG_PROXY_APPROVAL_RETENTION_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `86400` | Age in seconds after which a resolved approval is swept from the proxy store. Stale pending approvals (no longer confirmed by the engine) age from creation; approvals the engine still reports as pending are always kept so a human can still decide. |

---

### `UNDOLOG_PROXY_ENGINE_GRPC_ADDR`

| Type | Default | Required |
|------|---------|----------|
| `string` | `localhost:50051` | Yes |

Address of the Rust UndoLog Engine gRPC endpoint. Used by the proxy to forward interception, commit, fail, approve, and reject requests.

---

### `UNDOLOG_PROXY_UPSTREAM_TOOL_URL`

| Type | Default | Required |
|------|---------|----------|
| `string` | `""` | No (required for tool execution) |

HTTP endpoint that receives forwarded tool calls. When empty, tool execution is not available.

---

### `UNDOLOG_LOG_LEVEL`

| Type | Default | Valid Values |
|------|---------|-------------|
| `string` | `info` | `debug`, `info`, `warn`, `error` |

Log level for the proxy server. Shared env var name across both engine and proxy.

---

### `UNDOLOG_PROXY_API_KEYS`

| Type | Default | Description |
|------|---------|-------------|
| `string` | `""` | Comma-separated `key=org_id` pairs for API key authentication. Example: `sk-abc123=org-xyz,sk-def456=org-uvw` |

---

## Shared

### `UNDOLOG_LOG_LEVEL`

| Type | Default | Services |
|------|---------|----------|
| `string` | `info` | Engine, Proxy |

Shared log level variable. When set, affects both the Rust engine and Go proxy.

---

## Environment Variable Inventory

| Variable | Service | Type | Default | Required |
|----------|---------|------|---------|----------|
| `DATABASE_URL` | Engine | `string` | `postgresql://postgres:postgres@localhost:5432/undolog_dev` | Yes |
| `UNDOLOG_ENGINE_GRPC_ADDR` | Engine | `string` | `0.0.0.0:50051` | Yes |
| `UNDOLOG_ENGINE_HEALTH_ADDR` | Engine | `string` | `0.0.0.0:9090` | Yes |
| `UNDOLOG_LOG_LEVEL` | Engine, Proxy | `string` | `info` | No |
| `UNDOLOG_REGISTRY_REFRESH_SECS` | Engine | `uint64` | `60` | No |
| `UNDOLOG_LOCK_MAX_ATTEMPTS` | Engine | `uint32` | `3` | No |
| `UNDOLOG_LOCK_RETRY_MS` | Engine | `uint64` | `100` | No |
| `UNDOLOG_PROXY_LISTEN_ADDR` | Proxy | `string` | `:8080` | Yes |
| `UNDOLOG_PROXY_READ_TIMEOUT_SECS` | Proxy | `int` | `15` | No |
| `UNDOLOG_PROXY_WRITE_TIMEOUT_SECS` | Proxy | `int` | `15` | No |
| `UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS` | Proxy | `int` | `30` | No |
| `UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS` | Proxy | `int` | `30` | No |
| `UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS` | Proxy | `int` | `3` | No |
| `UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS` | Proxy | `int` | `100` | No |
| `UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE` | Proxy | `int` | `128` | No |
| `UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS` | Proxy | `int` | `60` | No |
| `UNDOLOG_PROXY_APPROVAL_RETENTION_SECS` | Proxy | `int` | `86400` | No |
| `UNDOLOG_PROXY_ENGINE_GRPC_ADDR` | Proxy | `string` | `localhost:50051` | Yes |
| `UNDOLOG_PROXY_UPSTREAM_TOOL_URL` | Proxy | `string` | `""` | No |
| `UNDOLOG_PROXY_API_KEYS` | Proxy | `string` | `""` | No |
