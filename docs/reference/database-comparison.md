---
title: "Database Comparison"
description: "Feature comparison across PostgreSQL, SQLite, and MySQL for UndoLog storage backends."
section: "reference"
---
# Database Comparison

Feature comparison across the three database backends evaluated for UndoLog.

---

## Summary

| Feature | PostgreSQL | SQLite | MySQL |
|---------|-----------|--------|-------|
| **Role** | Production | Development | Deferred |
| **Status** | Supported | Supported | Not implemented |
| **Advisory locks** | Yes | No | No |
| **Row-level security** | Yes | No | No |
| **Table partitioning** | Yes | No | Yes |
| **Connection pooling** | PgBouncer | Built-in | Built-in |
| **WAL streaming** | Yes | WAL mode | InnoDB redo |
| **Read replicas** | Yes | No | Yes |
| **JSON support** | JSONB | JSON | JSON |
| **Concurrency** | MVCC | WAL (single-writer) | MVCC |
| **Minimum version** | 16+ | 3.x | 8.0 |

---

## PostgreSQL

**Recommended for:** production deployments, multi-tenant environments, high-throughput workloads.

PostgreSQL is the primary storage backend. All UndoLog features are fully supported.

### Supported features

| Feature | Implementation |
|---------|---------------|
| Advisory locks | `pg_advisory_xact_lock()` for call signature deduplication |
| Row-level security | RLS policies on all data tables, `SET LOCAL undolog.current_org_id` per transaction |
| Partitioning | Monthly range partitions on `executed_at` for `undolog_effect_log` |
| Connection pooling | PgBouncer in transaction mode recommended |
| WAL archiving | Point-in-time recovery via `archive_mode = on` |
| Read replicas | Logical replication for read-heavy workloads |

### Limitations

- Requires a running PostgreSQL instance (not embedded).
- Connection pooling configuration adds operational complexity.
- Advisory locks are session-scoped within a transaction, not portable to other databases.

---

## SQLite

**Recommended for:** local development, testing, CI pipelines, single-user tooling.

SQLite is the lightweight alternative for development. It eliminates the need for a database server.

### Supported features

| Feature | Implementation |
|---------|---------------|
| Effect logging | Full insert, query, commit, fail lifecycle |
| Session management | Create, read, update, state transitions |
| Approval workflow | Create, approve, reject, list pending |
| WAL journal mode | Enabled by default for concurrent reads |
| Async connection pooling | `sqlx::SqlitePool` with configurable connections |

### Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| No advisory locks | Concurrent writes with the same `call_signature` may not be detected | SQLite's `UNIQUE` constraint provides deduplication at the schema level |
| No row-level security | Multi-tenant isolation is not enforced at the database level | Acceptable for single-user development only |
| No table partitioning | Effect log grows without bounds | Acceptable for development volumes |
| Single-writer | WAL mode allows concurrent reads but serializes writes | Acceptable for development workloads |
| No read replicas | All reads hit the primary | Acceptable for development |

### When to use SQLite

- Running `cargo run` locally without PostgreSQL.
- CI pipelines that need a working engine without database setup.
- Unit and integration tests that do not test PostgreSQL-specific features.

### When NOT to use SQLite

- Production deployments.
- Multi-tenant environments.
- Workloads requiring concurrent writers.
- Testing PostgreSQL-specific behavior (advisory locks, RLS, partitioning).

---

## MySQL

**Status:** Not implemented. Deferred pending assessment.

MySQL was evaluated as a potential alternative to PostgreSQL. The assessment identified significant feature gaps.

### Assessment summary

| Feature | MySQL capability | Gap |
|---------|------------------|-----|
| Advisory locks | `GET_LOCK()` is session-scoped, not transaction-scoped | UndoLog requires transaction-scoped locks for crash safety |
| Row-level security | Not supported | Would require application-level enforcement |
| Table partitioning | Supported (RANGE, LIST, HASH) | Compatible |
| JSON support | JSON column type | Compatible |
| Connection pooling | Built-in thread pool or ProxySQL | Compatible |

### Recommendation

MySQL is not recommended as a storage backend at this time. The lack of transaction-scoped advisory locks means the deduplication guarantee would need to be implemented in application code, which is error-prone and reduces the crash-safety guarantees that PostgreSQL provides.

If MySQL support is needed in the future, the storage trait abstraction in `undolog-store` would need to be extended with a MySQL adapter that implements alternative deduplication logic.

---

## Feature details

### Advisory locks

Advisory locks prevent concurrent tool calls with the same signature from being executed multiple times. This is the core deduplication mechanism.

| Database | Mechanism | Scope | Crash-safe |
|----------|-----------|-------|-----------|
| PostgreSQL | `pg_advisory_xact_lock()` | Transaction | Yes (released on commit/rollback) |
| SQLite | `UNIQUE` constraint on `call_signature` | Schema | Yes (constraint is persistent) |
| MySQL | `GET_LOCK()` | Session | No (released on disconnect) |

PostgreSQL's advisory locks are the gold standard: they are transaction-scoped, automatically released on crash, and do not require schema changes. SQLite's `UNIQUE` constraint provides equivalent deduplication at the schema level but does not block concurrent inserts (the second insert simply fails with a constraint violation, which the engine handles as a replay).

### Row-level security

RLS ensures tenant isolation at the database level. Without RLS, a bug in the application layer could leak data across tenants.

| Database | RLS support | UndoLog usage |
|----------|------------|---------------|
| PostgreSQL | Full RLS | All data tables have RLS policies |
| SQLite | Not supported | N/A (single-user development) |
| MySQL | Not supported | N/A |

### Partitioning

The effect log grows monotonically and can reach billions of rows in production. Partitioning keeps query performance stable.

| Database | Partitioning | UndoLog usage |
|----------|-------------|---------------|
| PostgreSQL | Native (RANGE, LIST, HASH) | Monthly range partitions on `executed_at` |
| SQLite | Not supported | N/A (development volumes) |
| MySQL | Native (RANGE, LIST, HASH) | Would be compatible if MySQL were supported |

---

## Migration between backends

There is no automated migration path between database backends. Each backend is designed for a specific use case:

- **SQLite to PostgreSQL:** Export data using `sqlite3 .dump` and import with `psql`. Schema differences (enum types, partitioning, RLS) require manual adjustment.
- **PostgreSQL to SQLite:** Not recommended. Partitioned tables, RLS policies, and advisory lock semantics do not transfer.

The recommended workflow is: develop locally with SQLite, test against PostgreSQL in CI, deploy with PostgreSQL in production.
