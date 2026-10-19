---
title: "UndoLog 0.1.0 release notes"
description: "Release notes for UndoLog 0.1.0 covering context-var session injection, proxy resilience, single-binary deployment, SQLite support, gRPC auth, and observability."
section: "changelog"
---
# UndoLog 0.1.0 release notes

UndoLog 0.1.0 is the initial release of UndoLog, establishing the core safety layer for AI agents across SDK usability, proxy reliability, local developer workflows, and operational resilience. This release introduces context-var session injection for Python, deterministic cross-language BLAKE3 idempotency parity, an in-process SQLite engine adapter, a robust proxy interceptor plugin system, and end-to-end gRPC authentication.

## Highlights

### Context-Var Session Injection and Framework Wrappers

The Python SDK now supports context-var session management via `run_with_session`, eliminating the need to explicitly pass `_session` through every tool call ([ADR 0015](../adr/0015-context-var-session-injection.md)). First-party wrappers for popular agent frameworks allow one-line instrumentation:
- `wrap_langgraph`: Runs compiled LangGraph graphs inside UndoLog sessions with pending approval state reflection.
- `wrap_crewai`: Automatic session creation and tool tier interception for CrewAI agents.
- `wrap_semantic_kernel`: Instruments Microsoft Semantic Kernel plugins.
- `wrap_llamaindex`: Wraps LlamaIndex tools and manages approval interrupts.

Adopters can test compensation sagas locally in CI without external dependencies using `test_compensation` and `test_saga` ([ADR 0017](../adr/0017-compensation-testing-harness.md)).

### Cross-Language BLAKE3 Idempotency Parity

The TypeScript SDK and Python SDK now share byte-for-byte identical BLAKE3 tool call signatures across 49 cross-language test vectors, covering unicode keys, float edge cases, nested collections, and arbitrary JSON payloads ([ADR 0008](../adr/0008-cross-language-canonical-json-fuzz-testing.md)). SAFE-tier tools no longer consume session step indices in Python, achieving behavioral parity with TypeScript.

### Go Proxy Hardening and Rate Limiting

The Go proxy has undergone extensive hardening for production traffic:
- **Authentication:** Authenticates to the engine via `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` ([ADR 0018](../adr/0018-proxy-grpc-auth-method.md)).
- **Plugin Interceptors:** Modular interceptor chain with configurable execution order, header manipulation, and rejection policies ([ADR 0019](../adr/0019-proxy-plugin-system.md)).
- **Rate Limiting & Backpressure:** Per-API-key rate limiting with 429 and `Retry-After` headers, combined with bounded queues and circuit breakers to protect database pools ([ADR 0020](../adr/0020-proxy-rate-limiting.md)).
- **Resilience:** Lazy gRPC dials with automatic reconnection and a `/readyz` endpoint for orchestration health probes.
- **Server-Sent Events:** Durable `GET /events` stream with proper flusher handling, client disconnect cleanup, and authenticated session filtering.

### Engine Storage Flexibility and Sagas

- **SQLite Storage Adapter:** The engine now supports an in-process SQLite storage adapter behind the `sqlite` feature flag, enabling zero-infrastructure local runs and fast unit test cycles ([ADR 0010](../adr/0010-postgresql-optional-dependency.md)).
- **Standalone Migration CLI:** `undolog-migrate` manages database schema lifecycles (`up`, `down`, `status`) using SHA-384 checksums and advisory locks ([ADR 0009](../adr/0009-migration-cli-design.md)).
- **Dead-Letter Queue:** Permanently failed compensations are isolated into a dedicated dead-letter table with administrative inspection, retry, and skip RPCs ([ADR 0013](../adr/0013-dead-letter-queue.md)).
- **Retention & Timeouts:** Automated effect log TTL retention ([ADR 0012](../adr/0012-effect-ttl-retention.md)) and approval timeout background processing.

### Deployment and Observability

- **Unified Container Image:** Optional single-container deployment running both the Go proxy and Rust engine under one image for simplified edge and staging setups ([ADR 0021](../adr/0021-single-binary-deployment.md)).
- **Distributed Tracing & Metrics:** Native OpenTelemetry tracing with OTLP export and Prometheus `/metrics` endpoints.
- **Kubernetes Helm Chart:** Production-ready Helm chart with configurable autoscaling, ingress, and secret management.

## Breaking Changes

None. This is the initial release of UndoLog.

## Added

- SDK: `run_with_session` context manager for context-var session injection.
- SDK: Framework adapters (`wrap_langgraph`, `wrap_crewai`, `wrap_semantic_kernel`, `wrap_llamaindex`).
- SDK: In-process testing helpers `test_compensation` and `test_saga`.
- SDK: `undolog dev` local zero-dependency development server.
- Proxy: Interceptor plugin chain with configurable ordering and failure policies.
- Proxy: Token-based gRPC authentication with `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN`.
- Proxy: Per-API-key rate limiting with 429 responses and Prometheus metrics.
- Proxy: Bounded queue, concurrency cap, and circuit breaker answering 503.
- Proxy: `/readyz` endpoint for orchestration readiness checks.
- Engine: SQLite storage adapter for local testing and zero-dependency operation.
- Engine: `undolog-migrate` CLI tool for schema migrations.
- Engine: Dead-letter queue table and administrative gRPC endpoints.
- Engine: Configurable effect TTL retention policies.
- Engine: Automated approval timeout resolution with audit events.
- Infra: Single-container Docker Compose deployment option.
- Infra: OpenTelemetry tracing and Grafana dashboard assets.
- Infra: Helm chart for Kubernetes deployment.

## Changed

None. This is the initial release of UndoLog.

## Deprecated

None. This is the initial release of UndoLog.

## Removed

None. This is the initial release of UndoLog.

## Fixed

- SDK: Replay returns raw results matching Execute behavior ([ADR 0016](../adr/0016-replay-shape-contract.md)).
- SDK: `wrap_langgraph` echoes the position its run started from, so re-invoking with returned state replays completed steps.
- Proxy: Fixed stream write deadline timeouts on long-lived SSE connections.
- Proxy: Fixed connection leaks by ensuring HTTP client is closed on session exit.

## Security

- Proxy: Constant-time API-key verification using SHA-256 digests.
- Proxy: Enforced configurable request body and header size limits (413 Payload Too Large).
- Engine: Optional mutual token authentication for gRPC endpoints.
- Dependencies: Upgraded Next.js and Sharp dependencies to address upstream security advisories.

## Migration Guide

None required. This is the initial release of UndoLog. For installation and getting started instructions, see the [Quickstart](../getting-started/quickstart.md) and [Installation Guide](../getting-started/installation.md).

## Contributors

Thank you to all contributors who designed, implemented, and reviewed this release.
