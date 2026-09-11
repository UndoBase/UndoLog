//! SQLite schema initialization.
//!
//! Creates all required tables for local development. No advisory locks,
//! no RLS, no partitioning. TEXT is used for enum columns with CHECK
//! constraints to enforce valid values.

use sqlx::SqlitePool;
use tracing::info;
use undolog_types::errors::{UndoLogError, UndoLogResult};

/// Initialize the SQLite schema. Safe to call multiple times (IF NOT EXISTS).
pub async fn initialize(pool: &SqlitePool) -> UndoLogResult<()> {
    sqlx::raw_sql("PRAGMA journal_mode=WAL;")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("PRAGMA foreign_keys=ON;")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Effect log: core append-only log of every tool call.
        CREATE TABLE IF NOT EXISTS undolog_effect_log (
            effect_id         TEXT PRIMARY KEY,
            org_id            TEXT NOT NULL,
            session_id        TEXT NOT NULL,
            tool_id           TEXT,
            call_signature    TEXT NOT NULL UNIQUE,
            tool_name         TEXT NOT NULL,
            tool_version      TEXT NOT NULL DEFAULT '1.0.0',
            tier              TEXT NOT NULL CHECK (tier IN ('safe', 'compensable', 'irreversible')),
            step_index        INTEGER NOT NULL,
            args_snapshot     TEXT NOT NULL,
            result_snapshot   TEXT,
            state             TEXT NOT NULL DEFAULT 'pending'
                            CHECK (state IN ('pending', 'executing', 'committed', 'compensating',
                                             'compensated', 'compensation_failed', 'approved',
                                             'rejected', 'replayed')),
            compensation_args TEXT,
            executed_at       TEXT NOT NULL DEFAULT (datetime('now')),
            committed_at      TEXT,
            compensated_at    TEXT,
            replay_count      INTEGER NOT NULL DEFAULT 0,
            last_replayed_at  TEXT,
            approval_request_id TEXT
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_vel_session ON undolog_effect_log (session_id, step_index);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        "CREATE INDEX IF NOT EXISTS idx_vel_state ON undolog_effect_log (state, org_id);",
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_vel_org_time ON undolog_effect_log (org_id, executed_at DESC);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_vel_sig ON undolog_effect_log (call_signature);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Undo stack: ordered compensations per session (LIFO).
        CREATE TABLE IF NOT EXISTS undolog_undo_stack (
            undo_id              TEXT PRIMARY KEY,
            org_id               TEXT NOT NULL,
            session_id           TEXT NOT NULL,
            effect_id            TEXT NOT NULL,
            stack_position       INTEGER NOT NULL,
            compensation_fn      TEXT NOT NULL,
            compensation_version TEXT NOT NULL DEFAULT '1.0.0',
            compensation_args    TEXT NOT NULL,
            state                TEXT NOT NULL DEFAULT 'pending'
                               CHECK (state IN ('pending', 'running', 'compensated', 'failed', 'skipped')),
            retry_count          INTEGER NOT NULL DEFAULT 0,
            last_error           TEXT,
            registered_at        TEXT NOT NULL DEFAULT (datetime('now')),
            compensated_at       TEXT,
            max_retries          INTEGER NOT NULL DEFAULT 3,
            retry_backoff_ms     INTEGER NOT NULL DEFAULT 1000,
            UNIQUE (session_id, stack_position)
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_undoeffect ON undolog_undo_stack (effect_id);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Sessions: one agent execution session.
        CREATE TABLE IF NOT EXISTS undolog_sessions (
            session_id           TEXT PRIMARY KEY,
            org_id               TEXT NOT NULL,
            project_id           TEXT,
            external_run_id      TEXT,
            agent_name           TEXT,
            state                TEXT NOT NULL DEFAULT 'active'
                               CHECK (state IN ('active', 'completed', 'failed', 'compensating',
                                                'compensated', 'awaiting_approval', 'halted')),
            tool_calls_total     INTEGER NOT NULL DEFAULT 0,
            compensations_total  INTEGER NOT NULL DEFAULT 0,
            approvals_pending    INTEGER NOT NULL DEFAULT 0,
            started_at           TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at         TEXT,
            failed_at            TEXT,
            failure_reason       TEXT,
            metadata             TEXT NOT NULL DEFAULT '{}'
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_sess_org ON undolog_sessions (org_id);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_sess_state ON undolog_sessions (state);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        "CREATE INDEX IF NOT EXISTS idx_sess_started ON undolog_sessions (started_at DESC);",
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Approval requests: human-in-the-loop gate for irreversible actions.
        CREATE TABLE IF NOT EXISTS undolog_approval_requests (
            approval_request_id  TEXT PRIMARY KEY,
            org_id               TEXT NOT NULL,
            session_id           TEXT NOT NULL,
            effect_id            TEXT NOT NULL,
            tool_name            TEXT NOT NULL,
            irreversibility_reason TEXT NOT NULL,
            risk_tags            TEXT NOT NULL DEFAULT '[]',
            estimated_impact     TEXT,
            proposed_args        TEXT NOT NULL,
            agent_context        TEXT NOT NULL DEFAULT '{}',
            state                TEXT NOT NULL DEFAULT 'pending'
                               CHECK (state IN ('pending', 'approved', 'rejected', 'timed_out', 'auto_approved')),
            timeout_at           TEXT NOT NULL,
            auto_approve_on_timeout INTEGER NOT NULL DEFAULT 0,
            resolved_at          TEXT,
            resolved_by          TEXT,
            approved_args        TEXT,
            created_at           TEXT NOT NULL DEFAULT (datetime('now'))
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        "CREATE INDEX IF NOT EXISTS idx_appr_session ON undolog_approval_requests (session_id);",
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        "CREATE INDEX IF NOT EXISTS idx_appr_timeout ON undolog_approval_requests (timeout_at);",
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Approval events: immutable audit trail.
        CREATE TABLE IF NOT EXISTS undolog_approval_events (
            event_id             TEXT PRIMARY KEY,
            approval_request_id  TEXT NOT NULL,
            org_id               TEXT NOT NULL,
            action               TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'modify', 'timeout')),
            actor                TEXT NOT NULL,
            note                 TEXT,
            args_diff            TEXT,
            occurred_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        "CREATE INDEX IF NOT EXISTS idx_appevt_req ON undolog_approval_events (approval_request_id, occurred_at);",
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Tool registry: tier annotations for registered tools.
        CREATE TABLE IF NOT EXISTS undolog_tool_registry (
            tool_id              TEXT PRIMARY KEY,
            org_id               TEXT NOT NULL,
            project_id           TEXT,
            tool_name            TEXT NOT NULL,
            tool_version         TEXT NOT NULL DEFAULT '1.0.0',
            tier                 TEXT NOT NULL CHECK (tier IN ('safe', 'compensable', 'irreversible')),
            irreversibility_reason TEXT,
            compensation_ref     TEXT,
            tool_schema          TEXT NOT NULL DEFAULT '{}',
            risk_tags            TEXT NOT NULL DEFAULT '[]',
            estimated_impact     TEXT,
            registered_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
            is_active            INTEGER NOT NULL DEFAULT 1,
            UNIQUE (org_id, tool_name, tool_version)
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_toolorg ON undolog_tool_registry (org_id);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql("CREATE INDEX IF NOT EXISTS idx_toolname ON undolog_tool_registry (tool_name);")
        .execute(pool)
        .await
        .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    sqlx::raw_sql(
        r#"
        -- Schema migrations tracking.
        CREATE TABLE IF NOT EXISTS undolog_schema_migrations (
            version      TEXT PRIMARY KEY,
            description  TEXT NOT NULL,
            applied_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        "#,
    )
    .execute(pool)
    .await
    .map_err(|e| UndoLogError::Internal(e.to_string()))?;

    info!("SQLite schema initialized");
    Ok(())
}
