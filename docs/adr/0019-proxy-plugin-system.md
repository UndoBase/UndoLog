---
title: "ADR 0019: Middleware Plugin System"
description: "- **Date:** 2026-10-11 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0019: Middleware Plugin System

- **Date:** 2026-10-11
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The proxy's middleware chain is assembled in code: panic recovery
wraps request IDs, which wrap structured logging, which wraps
API-key authentication, which wraps the protected routes. Every
deployment runs the same chain, so adding request policy (rate
limiting, a custom auth header, blocking a source range) means
editing proxy code and rebuilding.

The chain mixes deployment policy with process guarantees. Rate
limits, header rules, and source-range blocks are policy a
deployment may want to change; panic recovery, request IDs,
authentication, and preserved flushing are guarantees every
deployment must keep. The response wrapper forwards
`http.Flusher` and exposes `Unwrap`, so Server-Sent Events stream
through the chain, and request IDs correlate every log line. A
design that made the guarantees optional would trade real safety
for convenience.

Requirements:

- A deployment adds request policy without forking the proxy.
- Plugin order is configuration, not code.
- A plugin can modify a request, reject it, modify the response, and
  log, without gaining access to effect state.
- Rejections keep one error format and one log line.
- Streaming responses stay untouched.

## Decision

Each plugin is a value implementing one interface:

```go
type Plugin interface {
    Name() string
    Before(r *http.Request) Decision
    After(w http.ResponseWriter, r *http.Request)
}
```

The four capabilities map onto it as follows. `Before` modifies the
request in place or returns a rejection. `Before` and `After` log
through a `*slog.Logger` injected when the plugin is constructed, so
plugins need no globals. `After` mutates response headers only.

`Decision` allows or rejects; a rejection carries the HTTP status
and a short reason. The chain handles the rest: a rejection stops
later `Before` hooks and the handler, writes the proxy's standard
JSON error, and emits one structured log line naming the plugin and
the reason. `After` hooks never run for a rejection, because no
downstream response exists to modify.

The chain executes `Before` hooks in configured order. It wraps the
response writer and, when the handler first commits the response,
runs every `After` hook in reverse chain order before the headers
reach the wire, so headers commit exactly once. Everything after
commit, including SSE flushing, passes through untouched.

Plugins mount inside the existing chain, after authentication, so
`Before` hooks see the resolved organization. `/health` and
`/metrics` stay outside. Panic recovery, request IDs, and structured
logging stay hard-coded middlewares rather than plugins: they are
process guarantees, and logging must also cover requests that
authentication rejects before any plugin runs.

The plugin list is configuration. An empty list is exactly today's
behaviour; an unknown name is a startup error naming the valid
options, not a runtime surprise. The list arrives as the
`UNDOLOG_PROXY_PLUGINS` environment variable, a comma-separated list
of plugin names, and gains a config-file equivalent when the proxy
gains file configuration.

Built-ins: a header plugin adds a header to every request and doubles
as the worked example; rate limiting arrives later as its own plugin;
request logging remains the structured-logging middleware, not a
plugin.

## Alternatives Considered

### Alternative 1: In-Process Interface With a Compiled Registry (Chosen)

- **Pros:** Hooks run in the request's goroutine with typed access to
  the request, the response, the resolved organization, and the
  injected logger. No new process, protocol, or dependency, and the
  order tests run as ordinary unit tests.
- **Cons:** A new plugin means writing Go, registering it, and
  rebuilding the proxy image. The interface cannot express body
  transformation or streaming control, by design.
- **Chosen?** Yes. Deployments already build the proxy image, and a
  compiled hook keeps the request path free of extra hops and failure
  modes.

### Alternative 2: Runtime-Loaded Go Plugins (Rejected)

- **Pros:** A separately built shared object loads without rebuilding
  the proxy.
- **Cons:** The Go plugin mechanism requires the plugin and the proxy
  to be built with the same Go toolchain and target operating system,
  and fails at load time in ways operators cannot easily diagnose.
  Container deployments would still bake the shared object into an
  image.
- **Chosen?** No. The fragility buys nothing the rebuild workflow does
  not already provide.

### Alternative 3: Out-of-Process Hooks Over HTTP (Rejected)

- **Pros:** Any language can write a hook, and hooks deploy
  independently of the proxy.
- **Cons:** Every request, including long-lived SSE streams, pays a
  network hop and gains a new remote failure mode. The hook service
  becomes a second availability requirement on the safety path.
- **Chosen?** No. The latency and operational surface outweigh the
  language freedom for policy that is naturally request-scoped.

## Consequences

**Positive:** Rate limiting, header policy, and source-range blocks
land as small plugins instead of proxy rewrites. Rejections share one
error envelope and one log line. Order, short-circuiting, and header
timing are unit-testable without a network.

**Negative:** Operators who need a new plugin must build it into the
proxy image. Response hooks cannot adjust a response after commit,
so status-code policies must run in `Before`.

**Risks:** A plugin could panic or block. Mitigation: panic recovery
sits outermost in the chain, so a panic becomes the standard error
response, and the plugin documentation requires hooks to be quick
and non-blocking. A misordered list could put policy before
authentication. Mitigation: the mounting position is fixed, not
configurable: hooks always run after authentication, and the
configuration reference states it.

## References

- [ADR 0002](0002-go-for-mcp-proxy.md): the proxy is a single Go
  binary, which the compiled registry fits
- [Configuration reference](../reference/configuration.md): where the
  plugin list and worked examples are documented
