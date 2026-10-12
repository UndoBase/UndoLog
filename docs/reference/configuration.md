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

### `UNDOLOG_ENGINE_AUTH_TOKEN`

| Type | Default | Required |
|------|---------|----------|
| `string` | `""` | No |

Shared secret checked on every gRPC call. When set, a call must present it as
`Bearer <token>` in the `authorization` metadata or is answered with
`UNAUTHENTICATED`; an empty or blank value disables the check.

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

### `UNDOLOG_PROXY_READ_HEADER_TIMEOUT_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `15` | Seconds the server waits to read request headers before it drops the connection. Guards against slow-loris style timeouts. |

---

### `UNDOLOG_PROXY_IDLE_TIMEOUT_SECS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `60` | Seconds a keep-alive connection may sit idle between requests before the server closes it. |

---

### `UNDOLOG_PROXY_MAX_HEADER_BYTES`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `1048576` | Maximum total bytes the server parses for request headers, including the request line. |

---

### `UNDOLOG_PROXY_MAX_BODY_BYTES`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `1048576` | Maximum request body size in bytes for `/mcp/tool_call` and the approval decision endpoints. Larger bodies are rejected with HTTP `413`. |

---

### `UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `3` | Maximum number of attempts for transient Commit/Fail RPC failures. The engine connection itself is created lazily and reconnects automatically, so it has no retry budget. Deterministic failures (for example an invalid state transition) are never retried. Setting this to `1` disables Commit/Fail retries. |

---

### `UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS`

| Type | Default | Description |
|------|---------|-------------|
| `int` | `100` | Base delay in milliseconds between Commit/Fail retry attempts, scaled linearly per attempt. |

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

### `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN`

| Type | Default | Required |
|------|---------|----------|
| `string` | `""` | No |

Shared secret presented on every engine gRPC call as `Bearer <token>`. Must
match the engine's `UNDOLOG_ENGINE_AUTH_TOKEN`; an empty or blank value sends
no credential.

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
| `string` | `""` | Comma-separated `key=org_id` pairs for API key authentication. Example: `sk-abc123=org-xyz,sk-def456=org-uvw`. At least one pair is required: the proxy refuses to start with none configured, and keys are compared as SHA-256 digests in constant time. |

---

### `UNDOLOG_PROXY_PLUGINS`

| Type | Default | Required |
|------|---------|----------|
| `string` | `""` | No |

Comma-separated interceptor plugin names in execution order, for example
`header,block-range`. The chain runs after authentication, so request hooks
see the resolved organization; `/health` and `/metrics` are never proxied
and stay outside it. An empty value runs no plugins, and an unknown name
stops the proxy at startup with the valid options. Built-in: `header` adds
`X-Custom-Header: undolog` to every proxied request. Rate limiting is not
selected here; it has its own settings below.

---

### `UNDOLOG_PROXY_RATE_LIMIT_RPS`

| Type | Default | Required |
|------|---------|----------|
| `float` | `0` | No |

Sustained requests per second allowed per API key, for example `50`. The
proxy meters each key by its constant-time-matched digest, never the raw
key, and mounts the limiter whenever this value is positive, independently
of `UNDOLOG_PROXY_PLUGINS`; `0` disables it. Must be set together with
`UNDOLOG_PROXY_RATE_LIMIT_BURST`, and a malformed or negative value stops
the proxy at startup instead of silently dropping the limit. A request
over its bucket is answered with `429`, a `Retry-After` header holding the
wait in whole seconds, and the standard error envelope with code
`plugin_rejected` and message `rate limit exceeded`. Rejections are
counted on `/metrics` as `undolog_proxy_rate_limit_rejections_total`.
This limiter is the proxy's only source of `429`.

---

### `UNDOLOG_PROXY_RATE_LIMIT_BURST`

| Type | Default | Required |
|------|---------|----------|
| `int` | `0` | No |

Token bucket size per API key: how many requests one key may send at once
after an idle period, for example `100`. Must be set together with
`UNDOLOG_PROXY_RATE_LIMIT_RPS`; `0` disables the limiter.

---

### Custom plugin examples

Plugins compile into the proxy image: implement the interface in
`services/undolog-proxy/internal/plugin`, register the constructor under a
name in `builtins` in `plugin.go`, and rebuild. Both examples below are
complete implementations of `Name`, `Before`, and `After`.

Add a header to every request:

```go
// upstreamAuth adds a shared header the upstream tool server can require.
type upstreamAuth struct{ token string }

// Name returns the configuration name of the plugin.
func (upstreamAuth) Name() string { return "upstream-auth" }

// Before adds the header to the outbound request.
func (p upstreamAuth) Before(r *http.Request) Decision {
	r.Header.Set("X-Upstream-Auth", p.token)
	return Decision{}
}

// After leaves responses unchanged.
func (upstreamAuth) After(http.ResponseWriter, *http.Request) {}
```

Register it as
`"upstream-auth": func(*slog.Logger) Plugin { return upstreamAuth{token: "shared-secret"} }`,
then set `UNDOLOG_PROXY_PLUGINS=upstream-auth`.

Reject requests from an IP range:

```go
// blockRange rejects requests whose source address falls inside one CIDR.
type blockRange struct{ network *net.IPNet }

// newBlockRange builds the plugin at startup and rejects an invalid CIDR
// there instead of at request time.
func newBlockRange(cidr string) Plugin {
	_, network, err := net.ParseCIDR(cidr)
	if err != nil {
		panic("block-range: invalid CIDR " + cidr)
	}
	return blockRange{network: network}
}

// Name returns the configuration name of the plugin.
func (blockRange) Name() string { return "block-range" }

// Before rejects an address inside the configured network.
func (p blockRange) Before(r *http.Request) Decision {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return Decision{}
	}
	if ip := net.ParseIP(host); ip != nil && p.network.Contains(ip) {
		return Decision{Status: http.StatusForbidden, Reason: "source address not allowed"}
	}
	return Decision{}
}

// After leaves responses unchanged.
func (blockRange) After(http.ResponseWriter, *http.Request) {}
```

Register it as
`"block-range": func(*slog.Logger) Plugin { return newBlockRange("203.0.113.0/24") }`,
then set `UNDOLOG_PROXY_PLUGINS=header,block-range`.

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
| `UNDOLOG_ENGINE_AUTH_TOKEN` | Engine | `string` | `""` | No |
| `UNDOLOG_LOG_LEVEL` | Engine, Proxy | `string` | `info` | No |
| `UNDOLOG_REGISTRY_REFRESH_SECS` | Engine | `uint64` | `60` | No |
| `UNDOLOG_LOCK_MAX_ATTEMPTS` | Engine | `uint32` | `3` | No |
| `UNDOLOG_LOCK_RETRY_MS` | Engine | `uint64` | `100` | No |
| `UNDOLOG_PROXY_LISTEN_ADDR` | Proxy | `string` | `:8080` | Yes |
| `UNDOLOG_PROXY_READ_TIMEOUT_SECS` | Proxy | `int` | `15` | No |
| `UNDOLOG_PROXY_WRITE_TIMEOUT_SECS` | Proxy | `int` | `15` | No |
| `UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS` | Proxy | `int` | `30` | No |
| `UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS` | Proxy | `int` | `30` | No |
| `UNDOLOG_PROXY_READ_HEADER_TIMEOUT_SECS` | Proxy | `int` | `15` | No |
| `UNDOLOG_PROXY_IDLE_TIMEOUT_SECS` | Proxy | `int` | `60` | No |
| `UNDOLOG_PROXY_MAX_HEADER_BYTES` | Proxy | `int` | `1048576` | No |
| `UNDOLOG_PROXY_MAX_BODY_BYTES` | Proxy | `int` | `1048576` | No |
| `UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS` | Proxy | `int` | `3` | No |
| `UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS` | Proxy | `int` | `100` | No |
| `UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE` | Proxy | `int` | `128` | No |
| `UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS` | Proxy | `int` | `60` | No |
| `UNDOLOG_PROXY_APPROVAL_RETENTION_SECS` | Proxy | `int` | `86400` | No |
| `UNDOLOG_PROXY_ENGINE_GRPC_ADDR` | Proxy | `string` | `localhost:50051` | Yes |
| `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` | Proxy | `string` | `""` | No |
| `UNDOLOG_PROXY_UPSTREAM_TOOL_URL` | Proxy | `string` | `""` | No |
| `UNDOLOG_PROXY_API_KEYS` | Proxy | `string` | `""` | Yes |
| `UNDOLOG_PROXY_PLUGINS` | Proxy | `string` | `""` | No |
| `UNDOLOG_PROXY_RATE_LIMIT_RPS` | Proxy | `float` | `0` | No |
| `UNDOLOG_PROXY_RATE_LIMIT_BURST` | Proxy | `int` | `0` | No |
