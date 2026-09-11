---
title: "ADR 0010: PostgreSQL Optional Dependency Approach"
description: "- **Date:** 2026-09-11 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0010: PostgreSQL Optional Dependency Approach

- **Date:** 2026-09-11
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

PostgreSQL 16+ is currently a hard dependency for UndoLog. The engine uses PostgreSQL-specific features: advisory locks for tool call deduplication, row-level security for tenant isolation, and partitioned tables for the effect log. Developers who prefer MySQL, SQLite, or managed databases like Aurora must run a separate PostgreSQL instance solely for UndoLog, which creates friction for local development and limits adoption.

The Python SDK plan (PY-2) requires a local development mode that works without PostgreSQL. The infrastructure plan (INF-7) identifies PostgreSQL as an optional dependency for development, with SQLite as the lightweight alternative.

## Decision

PostgreSQL remains required for production deployments. SQLite is added as an optional dependency for local development and testing. MySQL support is deferred pending assessment.

## Alternatives Considered

### Alternative 1: PostgreSQL Required, SQLite for Development (Chosen)

- **Pros:** Production deployments retain full feature set (advisory locks, RLS, partitioning). SQLite provides zero-dependency local development. Feature parity is explicit: SQLite mode documents what is missing (no advisory locks, no RLS). The storage trait abstraction already exists in `undolog-store`, making adapter addition straightforward.
- **Cons:** Two code paths to maintain. SQLite adapter must be tested alongside PostgreSQL. Developers may not realize SQLite mode has limitations.
- **Chosen?** Yes. This matches the project's priority: production reliability with developer ergonomics.

### Alternative 2: Abstract All Database Features Behind Traits

- **Pros:** Maximum flexibility. Any database backend could be swapped in.
- **Cons:** Advisory locks, RLS, and partitioning are deeply PostgreSQL-specific. Abstracting them behind traits would require reimplementing equivalent semantics for each backend, which is unreliable and expensive. The benefit does not justify the complexity for a project at this stage.
- **Chosen?** No. The abstraction would be leaky and difficult to maintain.

### Alternative 3: MySQL as First-Class Alternative

- **Pros:** MySQL is widely used. Would broaden adoption.
- **Cons:** MySQL lacks advisory locks, RLS, and partitioning. The feature gap is larger than SQLite. MySQL's `GET_LOCK()` is session-scoped and does not support the transaction-scoped semantics UndoLog requires. Assessment (INF-7) has not been completed.
- **Chosen?** No. Deferred to a future assessment document.

## Consequences

**Positive:** Local development requires only `cargo run` with no database setup. SQLite adapter validates the storage trait abstraction. The feature comparison document clarifies exactly what works in each mode. The `sqlite` feature flag gates compilation, so SQLite dependencies are not compiled in production builds.

**Negative:** Two storage backends must be tested. SQLite limitations (no advisory locks, no RLS, no partitioning) must be documented prominently. Developers who test with SQLite may encounter different behavior in production.

**Risks:** The SQLite adapter may diverge from PostgreSQL behavior over time if not actively tested. Mitigation: run the same integration test suite against both backends with `#[cfg(feature = "sqlite")]` gating.

## References

- Infrastructure plan INF-7: PostgreSQL as Optional Dependency
- Python SDK plan PY-2: Local Development Mode
- `crates/undolog-store/src/lib.rs`: Storage trait definition
