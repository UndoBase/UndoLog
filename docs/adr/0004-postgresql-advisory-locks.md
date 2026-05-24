---
title: "ADR 0004: PostgreSQL Advisory Locks for Tool Call Deduplication"
description: "- **Date:** 2026-02-05 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0004: PostgreSQL Advisory Locks for Tool Call Deduplication

- **Date:** 2026-02-05
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

The engine must prevent two concurrent tool calls with the same call signature from both executing. If two agents or two replays of the same agent both invoke `send_email(to="alice", body="hi")` at the same time, exactly one should proceed and the other should be blocked or rejected. The locking mechanism must be atomic with the database insert that records the tool call attempt, there must be no window where a lock is acquired but the insert fails, or vice versa.

## Decision

Use `pg_try_advisory_xact_lock()` with a FNV-1a 64-bit key derived from the BLAKE3 call signature hash.

## Alternatives Considered

### Redis Distributed Lock (Redlock)

- **Pros:** Well-understood pattern, independent of the primary database, configurable TTL, client libraries available in all target languages.
- **Cons:** Adds Redis as an infrastructure dependency, increasing operational complexity. Every tool call intercept requires a network round trip to Redis on top of the database round trip. The lock acquisition is not atomic with the database INSERT, a crash between lock acquisition and insert leaves the lock orphaned until TTL expiry.
- **Chosen?** No. The additional infrastructure dependency and non-atomicity with the DB transaction are unacceptable.

### Application-Level Mutex (in-process mutex or channel)

- **Pros:** Zero latency, no external dependency, simple implementation.
- **Cons:** Does not survive process restart: if the engine restarts between tool call detection and execution, the lock is lost. Does not scale across multiple replicas of the engine.
- **Chosen?** No. UndoLog is designed for high-availability deployments with multiple engine replicas; process-local locking cannot provide cross-replica guarantees.

### SELECT ... FOR UPDATE on the call_signatures Row

- **Pros:** Standard PostgreSQL row-level locking, no separate lock mechanism needed. If the row exists, the lock is acquired as part of the transaction.
- **Cons:** The call signature may not yet have a row in the `call_signatures` table at the time of the intercept, it is created lazily on first invocation. Locking a non-existent row is impossible with `SELECT ... FOR UPDATE`. An `INSERT ... ON CONFLICT` pattern creates a deadlock risk where two concurrent transactions both attempt to insert and then lock.
- **Chosen?** No. The insert-before-lock pattern introduces deadlock scenarios that are difficult to resolve without serializing all concurrent inserts.

## Consequences

**Positive:** Atomic with the database transaction: the advisory lock is acquired and released within the same transaction scope. No additional infrastructure beyond PostgreSQL. Transaction-scoped release means the lock is automatically freed on `COMMIT` or `ROLLBACK`, eliminating orphaned lock cleanup. The `pg_try_advisory_xact_lock()` variant returns immediately (non-blocking), so the engine can fail fast rather than wait.

**Negative:** FNV-1a is a non-cryptographic hash. While 64-bit collisions are rare (approximately 2^32 pairs needed for 50% collision probability in a 64-bit space), they are mathematically possible. A collision would cause two different tool calls to contend on the same lock.

**Risks:** Under high concurrency with many tool calls sharing the same signature (e.g., many agents calling `read_file` simultaneously), lock contention could become a bottleneck. Mitigation: use additional bits from the signature for the lock key to distribute contention. PostgreSQL advisory locks are not visible in `pg_locks` as clearly as row-level locks, which can complicate debugging.
