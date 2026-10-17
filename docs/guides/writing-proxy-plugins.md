---
title: "Writing Proxy Plugins"
description: "How to author, configure, and register custom request and response interceptors for the UndoLog proxy."
section: "guides"
---

# Writing Proxy Plugins

The UndoLog proxy includes a configurable interceptor plugin chain. Deployments
use plugins to enforce request policy: such as injecting custom upstream
authentication headers, filtering client IP ranges, or inspecting responses:
without modifying core proxy routing or safety guarantees.

This guide walks you through the plugin architecture, the `Plugin` and
`Decision` interfaces, ordering and failure semantics, and two worked
examples: custom header injection and CIDR-based IP blocking.

For the design rationale behind in-process plugins, see
[ADR 0019: Middleware Plugin System](../adr/0019-proxy-plugin-system.md).

---

## Architecture Overview

The plugin chain executes inside the proxy's HTTP middleware pipeline:

1. **Outer Process Guarantees:** Panic recovery, request ID assignment (`X-Request-Id`),
   and structured request logging always wrap the pipeline. They cannot be
   bypassed or disabled by plugins.
2. **Authentication:** The proxy validates API keys and resolves the caller's
   organization before any plugin executes. `Before` hooks always have access
   to the authenticated organization.
3. **Plugin Chain:** Configured plugins execute their `Before` hooks in order.
   If any plugin rejects the request, downstream plugins and handlers are
   skipped immediately.
4. **Core Handlers:** The protected endpoints (`/mcp/tool_call`, `/approvals`,
   and `/events`) execute.
5. **Response Commit:** When the handler writes response headers, plugin `After`
   hooks execute in reverse chain order before bytes reach the network.
6. **Unproxied Endpoints:** The `/health`, `/readyz`, and `/metrics` endpoints
   are served outside the middleware stack. Plugins never intercept them.

```
Incoming Request
       │
       ▼
┌─────────────────────────────────────────┐
│ Outer Middleware                        │
│ (Panic Recovery, Request ID, Logging)   │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ Authentication                          │
│ (API Key validation, resolve Org ID)    │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ Plugin Chain: Before Hooks              │
│ (Order: Plugin 1 → Plugin 2 → Plugin N) │
└─────────────────────────────────────────┘
       │
       ├─────────────────► [Rejection] ──► Standard JSON Error
       ▼
┌─────────────────────────────────────────┐
│ Core Route Handler                      │
│ (/mcp/tool_call, /approvals, /events)   │
└─────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│ Plugin Chain: After Hooks (on commit)   │
│ (Order: Plugin N → Plugin 2 → Plugin 1) │
└─────────────────────────────────────────┘
       │
       ▼
Response to Client
```

---

## The Plugin Interface

Plugins are implemented in Go in the `services/undolog-proxy/internal/plugin`
package. Every plugin implements the `Plugin` interface:

```go
type Plugin interface {
    // Name returns the configuration identifier used in UNDOLOG_PROXY_PLUGINS.
    Name() string

    // Before runs before the route handler. It may inspect or mutate the
    // request in place, or return a Decision rejecting the request.
    Before(r *http.Request) Decision

    // After runs when the handler first commits the response. It may set or
    // mutate response headers only. It runs in reverse chain order.
    After(w http.ResponseWriter, r *http.Request)
}
```

### Hook Responsibilities

- **`Name() string`:** Returns the lowercase string identifier configured in
  the `UNDOLOG_PROXY_PLUGINS` environment variable (for example, `"header"` or
  `"block-range"`).
- **`Before(r *http.Request) Decision`:** Executes inline on the request
  goroutine. Keep hooks fast and non-blocking. It can mutate request headers,
  inspect the URL path, or return a non-zero `Decision` to reject the call.
- **`After(w http.ResponseWriter, r *http.Request)`:** Executes when the
  response is first committed (upon the first `WriteHeader` or `Write` call).
  It can mutate response headers on `w`. It never runs if a request was
  rejected by a `Before` hook.

---

## Decision and Rejection Semantics

A plugin communicates its verdict through the `Decision` struct:

```go
type Decision struct {
    // Status is the HTTP status code sent for a rejection (400 to 599).
    Status int

    // Reason explains the rejection. An empty string allows the request.
    Reason string

    // Headers are response headers written with the rejection.
    Headers map[string]string
}
```

### How Decisions Are Handled

1. **Allowing a Request:** Return an empty decision (`Decision{}`). The chain
   proceeds to the next plugin or handler.
2. **Rejecting a Request:** Return a decision with a non-empty `Reason` and an
   HTTP status code (such as `http.StatusForbidden` or `http.StatusBadRequest`).
3. **Status Code Normalization:** The status must be between 400 and 599. If a
   plugin specifies a status outside this range, the chain normalizes it to
   `403 Forbidden` to ensure invalid HTTP status codes never reach the wire.
4. **Short-Circuit:** A rejection stops execution immediately. Later plugins in
   the chain and the route handler do not run.
5. **Rejection Response:** The chain logs a warning, copies any headers from
   `Decision.Headers` to the response (such as `Retry-After`), and writes the
   standard UndoLog error envelope:

```json
{
  "request_id": "c1f7a4b2-8e12-4c63-b8d9-298f24419ad1",
  "code": "plugin_rejected",
  "message": "source address not allowed",
  "timestamp": "2026-10-18T00:00:00Z"
}
```

---

## Ordering and Failure Policy

### Execution Order

- **`Before` hooks** run in the exact order declared in `UNDOLOG_PROXY_PLUGINS`.
  For example, with `UNDOLOG_PROXY_PLUGINS=auth-header,ip-filter`, `auth-header`
  runs first, followed by `ip-filter`.
- **`After` hooks** run in **reverse order** (`ip-filter`, then `auth-header`).
  This ensures symmetry with layered middleware.

### Streaming Responses

The proxy streams lifecycle notifications over Server-Sent Events via `GET /events`.
The plugin chain wraps responses with a commit writer that executes `After`
hooks once upon initial header commit, then passes all subsequent `Write`,
`Flush`, and interface unwrapping calls through directly. Streaming connections
remain open without buffering.

### Failure Policy

- **Startup Validation:** The proxy validates all plugin names during startup
  in `plugin.New()`. If `UNDOLOG_PROXY_PLUGINS` contains an unknown name, the
  proxy exits immediately with a fatal error listing all valid plugin options.
- **Panics in Hooks:** If a plugin panics, the outermost panic recovery
  middleware catches it, logs a stack trace with the request identifier, and
  returns a `500 Internal Server Error` with `code: "internal_error"`. The proxy
  process remains running.

---

## Worked Examples

### Example 1: Custom Upstream Header Injection

This plugin adds a shared authorization secret to every request before it is
forwarded to the upstream tool server:

```go
package plugin

import "net/http"

// UpstreamAuthPlugin adds an internal authentication header to outbound requests.
type UpstreamAuthPlugin struct {
    token string
}

// NewUpstreamAuthPlugin constructs the plugin with a shared secret token.
func NewUpstreamAuthPlugin(token string) Plugin {
    return &UpstreamAuthPlugin{token: token}
}

// Name returns the configuration identifier.
func (p *UpstreamAuthPlugin) Name() string {
    return "upstream-auth"
}

// Before injects the header into the request.
func (p *UpstreamAuthPlugin) Before(r *http.Request) Decision {
    r.Header.Set("X-Upstream-Auth", p.token)
    return Decision{}
}

// After leaves response headers unmodified.
func (p *UpstreamAuthPlugin) After(w http.ResponseWriter, r *http.Request) {}
```

### Example 2: Source IP Range Blocking

This plugin checks client IP addresses against a disallowed CIDR block and
rejects matching traffic with `403 Forbidden`:

```go
package plugin

import (
    "net"
    "net/http"
)

// BlockRangePlugin rejects requests originating from a specific CIDR block.
type BlockRangePlugin struct {
    network *net.IPNet
}

// NewBlockRangePlugin parses the CIDR block at construction time.
func NewBlockRangePlugin(cidr string) Plugin {
    _, network, err := net.ParseCIDR(cidr)
    if err != nil {
        panic("block-range: invalid CIDR " + cidr)
    }
    return &BlockRangePlugin{network: network}
}

// Name returns the configuration identifier.
func (p *BlockRangePlugin) Name() string {
    return "block-range"
}

// Before inspects the remote address and rejects blocked subnets.
func (p *BlockRangePlugin) Before(r *http.Request) Decision {
    host, _, err := net.SplitHostPort(r.RemoteAddr)
    if err != nil {
        // Fall back to RemoteAddr if no port is present.
        host = r.RemoteAddr
    }
    ip := net.ParseIP(host)
    if ip != nil && p.network.Contains(ip) {
        return Decision{
            Status: http.StatusForbidden,
            Reason: "source address not allowed",
        }
    }
    return Decision{}
}

// After leaves response headers unmodified.
func (p *BlockRangePlugin) After(w http.ResponseWriter, r *http.Request) {}
```

---

## Registering and Enabling Plugins

Plugins are compiled directly into the Go proxy binary.

### 1. Register in Builtins

Open `services/undolog-proxy/internal/plugin/plugin.go` and add the plugin
constructor to the `builtins()` map:

```go
func builtins() map[string]func(*slog.Logger) Plugin {
    return map[string]func(*slog.Logger) Plugin{
        "header": func(*slog.Logger) Plugin { return headerPlugin{} },
        "upstream-auth": func(*slog.Logger) Plugin {
            return NewUpstreamAuthPlugin("shared-secret-key")
        },
        "block-range": func(*slog.Logger) Plugin {
            return NewBlockRangePlugin("203.0.113.0/24")
        },
    }
}
```

### 2. Compile the Proxy

Rebuild the proxy executable:

```bash
cd services/undolog-proxy
go build -o undolog-proxy .
```

### 3. Configure the Chain

Enable the desired plugins in `UNDOLOG_PROXY_PLUGINS`, separated by commas:

```bash
export UNDOLOG_PROXY_PLUGINS="upstream-auth,block-range"
./undolog-proxy
```

If an invalid name is provided (for example, `UNDOLOG_PROXY_PLUGINS=unknown`),
the proxy fails immediately on startup:

```
unknown plugin "unknown" in UNDOLOG_PROXY_PLUGINS; valid plugins: block-range, header, upstream-auth
```

---

## Related Documentation

- [Configuration Reference: `UNDOLOG_PROXY_PLUGINS`](../reference/configuration.md#undolog_proxy_plugins): environment variable reference for plugin configuration.
- [ADR 0019: Middleware Plugin System](../adr/0019-proxy-plugin-system.md): architectural decision record covering design tradeoffs.
- [Proxy REST API Reference](../reference/api/go-proxy-rest.md): details on endpoints wrapped by the plugin chain.
