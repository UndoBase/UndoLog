---
title: "ADR 0009: Standalone Migration CLI"
description: "- **Date:** 2026-09-11 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0009: Standalone Migration CLI

- **Date:** 2026-09-11
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

Migrations currently run only through `docker-entrypoint-initdb.d`. PostgreSQL's Docker image executes `.sql` files in alphabetical order on first startup. This approach has three problems:

1. **No rollback.** If a migration fails or introduces a regression, there is no automated way to revert. Operators must manually restore from a backup.
2. **No standalone execution.** Migrations cannot run outside Docker. This blocks zero-downtime deployments, CI/CD pipelines, and local development without Docker.
3. **No version tracking.** The `undolog_schema_migrations` table exists but is never checked or written to by the engine. The actual migration state is implicit: if the tables exist, migrations "ran."

The engine's `startup.rs` has a commented-out `sqlx::migrate!()` call (line 76) with a note that migrations "should be run separately before creating the engine." This ADR defines what that separate tool looks like.

## Decision

Build a standalone `undolog-migrate` Rust binary with three subcommands: `up`, `down`, and `status`. Use `sqlx::migrate!()` for embedded migration execution.

### CLI interface

```
undolog-migrate up [--database-url URL] [--dry-run]
undolog-migrate down [--database-url URL] [--dry-run]
undolog-migrate status [--database-url URL]
```

- `up`: applies all pending migrations in order. Idempotent (safe to run multiple times).
- `down`: rolls back the last applied migration. Idempotent.
- `status`: prints the current migration version, total applied count, and pending count.
- `--dry-run`: previews SQL without executing.
- `--database-url`: overrides `DATABASE_URL` env var.

### Migration tracking

Use `sqlx`'s built-in `_sqlx_migrations` table (created automatically by `sqlx::migrate!()`). This replaces the custom `undolog_schema_migrations` table, which will be dropped in a future migration after the CLI is established.

### Migration files

Keep the existing numbered files in `migrations/`:

```
migrations/
  0001_initial.sql
  0002_add_undo_stack_retry_fields.sql
  0003_seed_demo_data.sql
  0004_seed_demo_org_two.sql
```

New migrations follow the pattern `NNNN_description.sql`. The `sqlx::migrate!()` macro embeds these at compile time.

### Rollback design

Each migration must have a corresponding rollback expressed in the same file using a `-- migrate:down` marker:

```sql
-- migrate:up
ALTER TABLE undolog_undo_stack ADD COLUMN max_retries smallint NOT NULL DEFAULT 3;

-- migrate:down
ALTER TABLE undolog_undo_stack DROP COLUMN IF EXISTS max_retries;
```

`undolog-migrate down` reads the last applied migration, extracts the down section, and executes it.

### Concurrency safety

`undolog-migrate up` acquires a PostgreSQL advisory lock (`pg_advisory_lock(83729)`) before executing migrations. The lock number is an arbitrary constant defined in the migrate crate. If another migration process holds the lock, the CLI waits with a configurable timeout (default 30s) then exits with an error. This prevents concurrent migration runs from corrupting schema state.

### Integration with the engine

The engine remains unaware of migrations. Operators run `undolog-migrate up` before starting the engine. The engine's `startup.rs` continues to skip migrations (the commented-out `sqlx::migrate!()` call is removed entirely). This keeps the engine stateless with respect to schema management.

## Alternatives Considered

### Alternative 1: Standalone binary with embedded sqlx migrations (Chosen)

- **Pros:** Single binary, no external dependencies. sqlx handles version tracking, idempotency, and lock management. Rollback sections in migration files keep up/down paired.
- **Cons:** Requires a Rust build step. Rollback sections must be maintained manually in each migration file.
- **Chosen?** Yes. The Rust toolchain is already a build requirement. sqlx's migration infrastructure is battle-tested and avoids reinventing version tracking.

### Alternative 2: Uncomment `sqlx::migrate!()` in the engine

- **Pros:** Migrations run automatically on engine startup. No separate tool needed.
- **Cons:** Couples schema management to the engine process. Unsafe in multi-replica deployments (all replicas race to run migrations). No rollback support. No dry-run.
- **Chosen?** No. Automatic migration on startup is a known anti-pattern for production systems with multiple replicas.

### Alternative 3: Use a third-party migration tool (e.g., `refinery`, `alembic`)

- **Pros:** Mature tooling, community support.
- **Cons:** Adds a runtime dependency. Different migration file format than sqlx. Does not integrate with the existing `migrations/` directory structure.
- **Chosen?** No. The dependency cost outweighs the benefit when sqlx already provides what we need.

## Consequences

**Positive:** Operators gain a deterministic, auditable migration path that works outside Docker. Rollback support reduces recovery time from minutes (restore backup) to seconds (run `down`). Dry-run mode enables safe preview in CI/CD pipelines.

**Negative:** Migration files must now include `-- migrate:down` sections, adding maintenance overhead. The `undolog_schema_migrations` table becomes dead code and must be dropped.

**Risks:** The rollback sections could be incorrect or incomplete. Mitigation: integration tests that apply, verify, rollback, and verify again. The advisory lock could deadlock if a migration takes longer than the timeout. Mitigation: configurable timeout with a generous default.

## References

- plan/infrastructure.md, INF-1 (Standalone Migration CLI)
- plan/infrastructure.md, INF-10 (Migration Rollback Support)
- plan/infrastructure.md, INF-11 (Zero-Downtime Migration Tool)
- crates/undolog-engine/src/startup.rs (commented-out sqlx::migrate! call)
- migrations/0001_initial.sql (existing schema)
- report/analysis.md section 3.2 (blocker: no standalone migration tool)
