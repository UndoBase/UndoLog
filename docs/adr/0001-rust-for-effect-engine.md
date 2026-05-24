---
title: "ADR 0001: Rust for Effect Engine"
description: "- **Date:** 2026-01-15 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0001: Rust for Effect Engine

- **Date:** 2026-01-15
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The effect engine needs to handle concurrent tool call interception with advisory locks, maintain the call signature index, manage session state transitions, and run the approval timeout processor. Speed and memory safety are critical because the engine sits in the hot path of every tool call. The engine must also integrate with PostgreSQL 16+ for state persistence and support a gRPC interface for the proxy layer.

## Decision

Use Rust with the Tokio async runtime, sqlx for database access, and tonic for gRPC.

## Alternatives Considered

### Go

- **Pros:** Excellent concurrency primitives (goroutines), fast compilation, straightforward deployment.
- **Cons:** Weaker memory safety guarantees for the lock-heavy state machine. Garbage collection pauses, while small, introduce jitter that is problematic for advisory lock timing. No compile-time query verification against the database schema.
- **Chosen?** No. The GC jitter risk in the advisory lock retry loop and weaker safety guarantees for concurrent state access outweigh Go's ergonomic advantages.

### C++

- **Pros:** Same performance tier as Rust, mature ecosystem, no garbage collection.
- **Cons:** No dependency management equivalent to Cargo, no compile-time checked SQL queries (sqlx), manual memory management adds safety burden that is unacceptable for a safety-critical middleware component.
- **Chosen?** No. The safety burden and lack of modern tooling make C++ a poor fit for a project that prioritizes correctness guarantees.

### Python

- **Pros:** Fastest development iteration, largest ecosystem, easiest contributor onboarding.
- **Cons:** Too slow for the hot path. The advisory lock retry loop requires sub-millisecond precision which CPython cannot deliver. The GIL prevents true concurrent lock handling.
- **Chosen?** No. Performance requirements disqualify Python for this role.

## Consequences

**Positive:** Memory safety guarantees at compile time (no buffer overflows, use-after-free, or data races). Zero-cost abstractions mean the safety features do not add runtime overhead. Compile-time SQL verification against the actual database schema catches schema mismatches before deployment. Small binary size enables distroless Docker deployment.

**Negative:** Steeper learning curve for contributors who are not familiar with Rust's ownership model and borrow checker. Longer compile times compared to Go slow the inner development loop.

**Risks:** Rust ecosystem maturity for gRPC (tonic) is good but lags behind Go's grpc-go. If key dependencies become unmaintained, the project may need to fork them.
