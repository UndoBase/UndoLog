---
title: "ADR 0002: Go for MCP Proxy"
description: "- **Date:** 2026-01-22 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0002: Go for MCP Proxy

- **Date:** 2026-01-22
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The proxy translates HTTP/JSON from the Python SDK to gRPC/protobuf for the Rust engine. It also manages Server-Sent Events (SSE) dashboard streams, HTTP endpoints for approval workflows, and authentication middleware. The proxy is primarily I/O-bound: it multiplexes many concurrent connections, routes requests, and streams events.

## Decision

Use Go with the standard `net/http` package, `google.golang.org/grpc` for gRPC client connections, and `log/slog` for structured logging.

## Alternatives Considered

### Rust with Axum

- **Pros:** Type safety across the entire stack (shared protobuf types with the engine), excellent performance, memory safety.
- **Cons:** Adds significant compile-time overhead for what is primarily I/O routing with minimal business logic. The proxy is a thin translation layer; Rust's ownership model adds ceremony without proportional benefit.
- **Chosen?** No. The complexity overhead is not justified for a component that is fundamentally a protocol bridge with little domain logic.

### Node.js

- **Pros:** Excellent SSE support via native `EventSource`/`ReadableStream`, large ecosystem, fast for I/O workloads.
- **Cons:** Weaker type safety for the protocol translation layer. The proxy must faithfully translate between JSON schemas and protobuf definitions; TypeScript helps but still allows `any` escapes. Single-threaded event loop means CPU-bound protobuf serialization can block all connections.
- **Chosen?** No. The type safety gap and blocking serialization risk make Node.js less suitable for a middleware component.

### Python

- **Pros:** Could share types with the SDK layer, simple implementation.
- **Cons:** ASGI server performance lags behind Go's net/http for high-connection-count workloads. The GIL prevents true concurrent handling of many SSE streams.
- **Chosen?** No. Concurrency limitations make Python unsuitable for the proxy role.

## Consequences

**Positive:** Fast compilation: the proxy compiles in seconds, enabling rapid iteration. Goroutine-per-connection model handles thousands of concurrent SSE streams efficiently. Small, statically linked binary that deploys to a scratch Docker image. Wide community familiarity lowers the contributor bar.

**Negative:** gRPC protobuf types require manual conversion between Go structs and protobuf message types (no equivalent to tonic's auto-generated service traits). No compile-time SQL query verification (the proxy does not query the database directly, so this is acceptable).

**Risks:** Go's `google.golang.org/grpc` package has a large API surface; deprecation cycles can require non-trivial migration effort. If the proxy needs to perform stateful operations in the future, Go's lack of compile-time concurrency verification (unlike Rust's Send/Sync) becomes a risk.
