---
title: "Configuration Reference"
description: "All configuration is through environment variables, with an optional proxy configuration file. Organised by service."
section: "reference"
---
# Configuration Reference

All configuration is through environment variables, with an optional proxy
configuration file. Organised by service.

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

IP:port for the HTTP health endpoint. Every path answers `200 OK` with a JSON
body, and the two engine binaries answer with different bodies:

| Build | `GET /` response body |
|-------|-----------------------|
| `undolog-engine` (PostgreSQL) | `{"status":"ok","service":"undolog-engine","registry_age_secs":42}` |
| `undolog-engine-sqlite` (SQLite) | `{"status":"ok","service":"undolog-engine-sqlite"}` |

`registry_age_secs` counts the seconds since the last successful tool registry
refresh, or `null` before the first one. It appears only in the PostgreSQL
body: the SQLite binary is built with `--no-default-features --features sqlite`
and never starts the refresh loop the age measures, so the key is absent rather
than `null`.

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
| `uint64` | `15` | Interval in seconds between tool registry refreshes from the database. The engine loads `undolog_tool_registry` into an in-memory cache on startup and refreshes periodically at this interval. |

Values below `5` are rejected at startup: zero would panic the spawned
refresh task through `tokio::time::interval` and leave the registry stale
without a log line. The health payload's `registry_age_secs` field shows
how long the current registry data has been in memory.

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

### Configuration file

The proxy reads an optional TOML file selected by `UNDOLOG_PROXY_CONFIG`.
Environment variables override file values, and settings neither source
defines keep their built-in defaults.

| Source | Precedence |
|--------|------------|
| Environment variable | 1 |
| File named by `UNDOLOG_PROXY_CONFIG` | 2 |
| Built-in default | 3 |

File keys mirror the environment variable names with the `UNDOLOG_PROXY_`
prefix removed and lowercased, with the same units and value ranges:
`UNDOLOG_PROXY_READ_TIMEOUT_SECS` becomes `read_timeout_secs`, and
`UNDOLOG_LOG_LEVEL` becomes `log_level`. An unknown key, a value of the
wrong kind, a malformed file, or a path that does not exist all fail
startup naming the offending key or path, so a typo is never ignored.

```toml
listen_addr = ":9000"
engine_grpc_addr = "localhost:50051"
rate_limit_rps = 50
rate_limit_burst = 100
plugins = ["header"]

[api_keys]
"key-1" = "org-1"
```

`UNDOLOG_PROXY_API_KEYS` becomes the `[api_keys]` table instead of a
comma-joined value, and setting the variable replaces the file table as a
whole. TOML wins over YAML because implicit typing has no place in a
security tool's configuration, and over JSON because operators annotate
config files and JSON has no comments.

### Hot reload

When the proxy reads a configuration file it polls the file's modification
time every five seconds and applies new versions without a restart. Two
settings reload live.

| Setting | File keys | Effect of a reload |
|---------|-----------|--------------------|
| Log level | `log_level` | Verbosity changes in place for every log line. |
| Rate limit | `rate_limit_rps`, `rate_limit_burst` | Buckets clamp to the new size and refill at the new rate; setting both to zero turns metering off. |

Everything else shapes the already built process and needs a restart:
listen and engine addresses, timeouts, API keys, plugins, and the engine
backpressure mechanisms. A reload that changes one of them logs the file
keys involved and keeps the running values. Enabling rate limiting when
the proxy started without it also needs a restart, because the limiter
mounts into the plugin chain at startup.

A malformed or invalid new file is rejected as a whole: the proxy logs the
error and keeps the last good configuration until the next change.
Environment variables keep their precedence, so a value set in the
environment is never replaced by a reloaded file. Kubernetes ConfigMap
mounts work unchanged: the mount is a symlink to a versioned directory, and
the atomic symlink swap changes the modification time the proxy polls.

A reload is safe while requests are in flight. The structures a reload
touches are guarded for concurrent use: the log level swaps through an
atomic, and the limiter replaces its rate and bucket size under the
limiter's own lock. A request already in flight finishes against the
settings it started with; requests issued after the swap see the new
version.

---

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

### `UNDOLOG_PROXY_MAX_ENGINE_RPCS`

| Type | Default | Required |
|------|---------|----------|
| `int` | `0` | No |

Caps how many engine gRPC calls the proxy may have in flight at once, for
example `32`. This cap is the structural reason the engine's database
pool cannot exhaust: fewer requests can wait on the engine than the pool
can serve. A call answered while the gate is full receives `503`, a
`Retry-After` header, and the standard error envelope, counted on
`/metrics` as `undolog_proxy_engine_rejections_total` with label
`reason="gate"`. `0` disables the gate, keeping the previous unbounded
behavior. Must be positive to use `UNDOLOG_PROXY_ENGINE_QUEUE_MAX`.

---

### `UNDOLOG_PROXY_ENGINE_QUEUE_MAX`

| Type | Default | Required |
|------|---------|----------|
| `int` | `0` | No |

How many engine calls may wait for a free slot, for example `64`. A
micro-burst inside this depth waits for the next slot; a call that finds
the queue full is rejected immediately with `503` and `Retry-After`
(`reason="queue"`). Must be set together with
`UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS` and requires
`UNDOLOG_PROXY_MAX_ENGINE_RPCS`; `0` disables the queue.

---

### `UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS`

| Type | Default | Required |
|------|---------|----------|
| `int` | `0` | No |

How long one engine call may wait for a free slot, in milliseconds, for
example `250`. A call that waits this long without getting a slot is
rejected with `503` and `Retry-After`. Keep the budget small: waiting
longer than the caller's own `UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS` only
delays an answer the caller will never use. Must be set together with
`UNDOLOG_PROXY_ENGINE_QUEUE_MAX`; `0` disables the queue.

---

### `UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD`

| Type | Default | Required |
|------|---------|----------|
| `int` | `0` | No |

How many consecutive engine transport failures, for example `5`, open
the circuit breaker. Only connection loss counts as a failure: an error
the engine answered means it is reachable and resets the run. An open
breaker answers every guarded call with `503` and `Retry-After`
(`reason="breaker"`) without touching the engine, so a restarting engine
is not hammered. Background approval reconciliation is exempt. Must be
set together with `UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS`; `0`
disables the breaker.

---

### `UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS`

| Type | Default | Required |
|------|---------|----------|
| `int` | `0` | No |

How long the breaker stays open before probing the engine again, in
seconds, for example `30`. The probe is a single call: the first healthy
answer closes the breaker, and a failed probe reopens it for another
full interval. Must be set together with
`UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD`; `0` disables the breaker.

---

### Status codes for server capacity

The engine guard and the per-key limiter share one retry contract with
distinct codes, so operators can tell "clients are too fast" apart from
"the server is saturated":

| Status | Meaning | Sources |
|--------|---------|---------|
| `429` | The caller exceeded its own per-key rate | the per-key rate limiter |
| `503` | The server is at capacity | full gate, full queue, expired wait, open breaker, engine `RESOURCE_EXHAUSTED` |
| `502` | The engine failed | any other engine error |

Both `429` and `503` carry `Retry-After`, so SDK retry behavior is
identical either way; a `503` advertises one second and is a retryable
signal, not a guarantee of recovery. On the tool-call path the `503`
body is the standard error envelope whose code names the failed stage
(`intercept_failed` or `commit_failed`); the approve and reject
endpoints answer the plain text `engine at capacity`. Whenever a
mechanism is configured, `/metrics` publishes the
`undolog_proxy_engine_inflight`, `undolog_proxy_engine_queue_depth`,
and `undolog_proxy_engine_breaker_state` gauges (`0` closed, `1`
half-open, `2` open) from startup.

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
| `UNDOLOG_REGISTRY_REFRESH_SECS` | Engine | `uint64` | `15` | No |
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
| `UNDOLOG_PROXY_MAX_ENGINE_RPCS` | Proxy | `int` | `0` | No |
| `UNDOLOG_PROXY_ENGINE_QUEUE_MAX` | Proxy | `int` | `0` | No |
| `UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS` | Proxy | `int` | `0` | No |
| `UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD` | Proxy | `int` | `0` | No |
| `UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS` | Proxy | `int` | `0` | No |
