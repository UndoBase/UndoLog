//! SQLite EffectStore: effect log and undo stack operations.
//!
//! No advisory locks. Concurrency safety relies on SQLite WAL mode and
//! the UNIQUE constraint on `call_signature`.

use sqlx::sqlite::{SqliteConnection, SqlitePool};
use tracing::{debug, instrument, warn};

use undolog_types::{
    effect::{CallSignature, EffectRecord, EffectState, ToolCall, ToolResult},
    errors::{UndoLogError, UndoLogResult},
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId, ToolId, UndoId},
    saga::{SagaStepState, UndoEntry},
    tier::{CompensationDescriptor, ToolTier},
};

use super::util::parse_timestamp;

/// Repository for effect log and undo stack operations (SQLite backend).
#[derive(Clone)]
pub struct EffectStore {
    pool: SqlitePool,
}

impl EffectStore {
    /// Create a new effect repository over the given SQLite pool.
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Advisory lock is not available on SQLite. Returns `Ok(())` unconditionally.
    ///
    /// SQLite WAL mode provides sufficient concurrency safety for local
    /// development. The UNIQUE constraint on `call_signature` prevents
    /// duplicate inserts.
    #[instrument(skip(self), fields(signature = %_signature))]
    pub async fn acquire_advisory_lock(
        &self,
        _signature: &CallSignature,
        _max_attempts: u32,
        _retry_ms: u64,
    ) -> UndoLogResult<()> {
        Ok(())
    }

    /// Insert a Compensable tool call into the effect log.
    ///
    /// Returns `Ok(true)` if inserted, `Ok(false)` if a duplicate signature
    /// already exists (ON CONFLICT DO NOTHING).
    #[instrument(skip(self, call, compensation), fields(
        session_id = %call.session_id,
        tool_name  = %call.tool_name,
        step       = call.step_index,
    ))]
    pub async fn insert_compensable(
        &self,
        call: &ToolCall,
        signature: &CallSignature,
        effect_id: &EffectId,
        compensation: &CompensationDescriptor,
    ) -> UndoLogResult<bool> {
        let compensation_args = serde_json::to_value(&compensation.args)?;
        let args_snapshot = serde_json::to_value(&call.args)?;

        let rows = sqlx::query(
            r#"INSERT OR IGNORE INTO undolog_effect_log (
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version, tier,
                step_index, args_snapshot, compensation_args,
                state, executed_at
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, 'compensable',
                $8, $9, $10,
                'pending', datetime('now')
            )"#,
        )
        .bind(effect_id.to_string())
        .bind(call.org_id.to_string())
        .bind(call.session_id.to_string())
        .bind(call.tool_id.map(|id| id.to_string()))
        .bind(signature.as_str())
        .bind(&call.tool_name)
        .bind(&call.tool_version)
        .bind(call.step_index as i64)
        .bind(args_snapshot.to_string())
        .bind(compensation_args.to_string())
        .execute(&self.pool)
        .await?;

        Ok(rows.rows_affected() > 0)
    }

    /// Insert an Irreversible tool call (state = `pending`).
    ///
    /// Returns `Ok(true)` if inserted, `Ok(false)` if a duplicate signature
    /// already exists (ON CONFLICT DO NOTHING).
    #[instrument(skip(self, call), fields(
        session_id = %call.session_id,
        tool_name  = %call.tool_name,
    ))]
    pub async fn insert_irreversible(
        &self,
        call: &ToolCall,
        signature: &CallSignature,
        effect_id: &EffectId,
    ) -> UndoLogResult<bool> {
        let args_snapshot = serde_json::to_value(&call.args)?;

        let rows = sqlx::query(
            r#"INSERT OR IGNORE INTO undolog_effect_log (
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version, tier,
                step_index, args_snapshot,
                state, executed_at
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, 'irreversible',
                $8, $9,
                'pending', datetime('now')
            )"#,
        )
        .bind(effect_id.to_string())
        .bind(call.org_id.to_string())
        .bind(call.session_id.to_string())
        .bind(call.tool_id.map(|id| id.to_string()))
        .bind(signature.as_str())
        .bind(&call.tool_name)
        .bind(&call.tool_version)
        .bind(call.step_index as i64)
        .bind(args_snapshot.to_string())
        .execute(&self.pool)
        .await?;

        Ok(rows.rows_affected() > 0)
    }

    /// Push a compensation entry onto the undo stack.
    #[instrument(skip(self, call, compensation), fields(
        session_id = %call.session_id,
        fn_name    = %compensation.fn_name,
        step       = call.step_index,
    ))]
    pub async fn push_undo_entry(
        &self,
        call: &ToolCall,
        effect_id: &EffectId,
        compensation: &CompensationDescriptor,
    ) -> UndoLogResult<()> {
        let undo_id = UndoId::new();
        let compensation_args = serde_json::to_value(&compensation.args)?;

        sqlx::query(
            r#"INSERT INTO undolog_undo_stack (
                undo_id, org_id, session_id, effect_id,
                stack_position, compensation_fn, compensation_version,
                compensation_args, state, registered_at
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, 'pending', datetime('now')
            )"#,
        )
        .bind(undo_id.to_string())
        .bind(call.org_id.to_string())
        .bind(call.session_id.to_string())
        .bind(effect_id.to_string())
        .bind(call.step_index as i64)
        .bind(&compensation.fn_name)
        .bind(&compensation.fn_version)
        .bind(compensation_args.to_string())
        .execute(&self.pool)
        .await?;

        debug!(fn_name = %compensation.fn_name, "Undo stack entry registered");
        Ok(())
    }

    /// Mark an effect as executing.
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn set_executing(&self, org_id: &OrgId, effect_id: &EffectId) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"UPDATE undolog_effect_log
             SET state = 'executing'
             WHERE effect_id = $1 AND org_id = $2 AND state = 'pending'"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::InvalidStateTransition {
                effect_id: effect_id.to_string(),
                current_state: "not pending".to_string(),
                target_state: "executing".to_string(),
            });
        }
        Ok(())
    }

    /// Mark an effect as committed and cache the result.
    #[instrument(skip(self, result), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn commit_effect(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        result: ToolResult,
    ) -> UndoLogResult<()> {
        let result_json = serde_json::to_value(&result)?;

        let rows = sqlx::query(
            r#"UPDATE undolog_effect_log
             SET state = 'committed',
                 result_snapshot = $1,
                 committed_at = datetime('now')
             WHERE effect_id = $2 AND org_id = $3
               AND state IN ('executing', 'approved')"#,
        )
        .bind(result_json.to_string())
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?
        .rows_affected();

        if rows > 0 {
            return Ok(());
        }

        let exists: bool = sqlx::query_scalar(
            r#"SELECT EXISTS(SELECT 1 FROM undolog_effect_log WHERE effect_id = $1 AND org_id = $2)"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .fetch_one(&self.pool)
        .await?;

        if !exists {
            warn!(effect_id = %effect_id, "Commit called for non-existent effect (SAFE tier)");
            return Ok(());
        }
        Err(UndoLogError::NotExecuting { effect_id: effect_id.to_string() })
    }

    /// Record that a tool call failed; revert state to allow compensation.
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn fail_effect(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        reason: &str,
    ) -> UndoLogResult<()> {
        let error_json = serde_json::json!({ "error": reason });

        let rows = sqlx::query(
            r#"UPDATE undolog_effect_log
             SET state = 'pending',
                 result_snapshot = $1
             WHERE effect_id = $2 AND org_id = $3
               AND state IN ('executing', 'approved')"#,
        )
        .bind(error_json.to_string())
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?
        .rows_affected();

        if rows > 0 {
            return Ok(());
        }

        let exists: bool = sqlx::query_scalar(
            r#"SELECT EXISTS(SELECT 1 FROM undolog_effect_log WHERE effect_id = $1 AND org_id = $2)"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .fetch_one(&self.pool)
        .await?;

        if !exists {
            warn!(effect_id = %effect_id, "Fail called for non-existent effect (SAFE tier)");
            return Ok(());
        }
        Err(UndoLogError::NotExecuting { effect_id: effect_id.to_string() })
    }

    /// Find an effect record by call signature.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn find_by_signature(
        &self,
        org_id: &OrgId,
        signature: &CallSignature,
    ) -> UndoLogResult<Option<EffectRecord>> {
        let maybe_row = sqlx::query(
            r#"SELECT
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version, tier,
                step_index, args_snapshot, result_snapshot,
                state, compensation_args,
                executed_at, committed_at, compensated_at,
                replay_count, last_replayed_at, approval_request_id
             FROM undolog_effect_log
             WHERE call_signature = $1 AND org_id = $2
             LIMIT 1"#,
        )
        .bind(signature.as_str())
        .bind(org_id.to_string())
        .fetch_optional(&self.pool)
        .await?;

        maybe_row.map(map_effect_row).transpose()
    }

    /// Look up an effect record by its effect_id.
    #[instrument(skip(self, conn), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn find_by_effect_id(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<Option<EffectRecord>> {
        let maybe_row = sqlx::query(
            r#"SELECT
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version, tier,
                step_index, args_snapshot, result_snapshot,
                state, compensation_args,
                executed_at, committed_at, compensated_at,
                replay_count, last_replayed_at, approval_request_id
             FROM undolog_effect_log
             WHERE effect_id = $1 AND org_id = $2
             LIMIT 1"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .fetch_optional(conn)
        .await?;

        maybe_row.map(map_effect_row).transpose()
    }

    /// Increment the replay counter and update last_replayed_at.
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn mark_replayed(&self, org_id: &OrgId, effect_id: &EffectId) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_effect_log
             SET replay_count = replay_count + 1,
                 last_replayed_at = datetime('now'),
                 state = 'replayed'
             WHERE effect_id = $1 AND org_id = $2"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Link an approval request to an effect log entry.
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn set_approval_request_id(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        approval_request_id: &ApprovalRequestId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_effect_log
             SET approval_request_id = $1
             WHERE effect_id = $2 AND org_id = $3"#,
        )
        .bind(approval_request_id.to_string())
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition an Irreversible effect from pending to approved.
    #[instrument(skip(self, conn), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn approve_effect(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"UPDATE undolog_effect_log
             SET state = 'approved'
             WHERE effect_id = $1 AND org_id = $2 AND state = 'pending'"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(conn)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::InvalidStateTransition {
                effect_id: effect_id.to_string(),
                current_state: "not pending".to_string(),
                target_state: "approved".to_string(),
            });
        }
        Ok(())
    }

    /// Transition an Irreversible effect to rejected.
    #[instrument(skip(self, conn), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn reject_effect(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"UPDATE undolog_effect_log
             SET state = 'rejected'
             WHERE effect_id = $1 AND org_id = $2 AND state = 'pending'"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(conn)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::InvalidStateTransition {
                effect_id: effect_id.to_string(),
                current_state: "not pending".to_string(),
                target_state: "rejected".to_string(),
            });
        }
        Ok(())
    }

    /// Update args_snapshot for an approved effect.
    #[instrument(skip(self, conn, args), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn update_args_snapshot(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
        args: &serde_json::Value,
    ) -> UndoLogResult<()> {
        let args_json = serde_json::to_value(args)?;
        let rows = sqlx::query(
            r#"UPDATE undolog_effect_log
             SET args_snapshot = $1
             WHERE effect_id = $2 AND org_id = $3 AND state = 'approved'"#,
        )
        .bind(args_json.to_string())
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(conn)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::InvalidStateTransition {
                effect_id: effect_id.to_string(),
                current_state: "not approved".to_string(),
                target_state: "approved".to_string(),
            });
        }
        Ok(())
    }

    /// Load all pending undo entries for a session in LIFO order.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn load_undo_stack(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<Vec<UndoEntry>> {
        let rows = sqlx::query(
            r#"SELECT
                undo_id, org_id, session_id, effect_id,
                stack_position, compensation_fn, compensation_version,
                compensation_args, state, retry_count, last_error,
                registered_at, compensated_at,
                max_retries, retry_backoff_ms
             FROM undolog_undo_stack
             WHERE session_id = $1 AND org_id = $2 AND state = 'pending'
             ORDER BY stack_position DESC"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .fetch_all(&self.pool)
        .await?;

        rows.into_iter().map(map_undo_row).collect()
    }

    /// Mark an undo entry as compensated.
    #[instrument(skip(self), fields(org_id = %org_id, undo_id = %undo_id))]
    pub async fn mark_compensated(&self, org_id: &OrgId, undo_id: &UndoId) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_undo_stack
             SET state = 'compensated', compensated_at = datetime('now')
             WHERE undo_id = $1 AND org_id = $2"#,
        )
        .bind(undo_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Increment retry count and record the latest error message.
    #[instrument(skip(self), fields(org_id = %org_id, undo_id = %undo_id))]
    pub async fn record_compensation_retry(
        &self,
        org_id: &OrgId,
        undo_id: &UndoId,
        error: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_undo_stack
             SET retry_count = retry_count + 1, last_error = $1, state = 'running'
             WHERE undo_id = $2 AND org_id = $3"#,
        )
        .bind(error)
        .bind(undo_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Mark an undo entry as permanently failed.
    #[instrument(skip(self), fields(org_id = %org_id, undo_id = %undo_id))]
    pub async fn mark_compensation_failed(
        &self,
        org_id: &OrgId,
        undo_id: &UndoId,
        reason: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_undo_stack
             SET state = 'failed', last_error = $1
             WHERE undo_id = $2 AND org_id = $3"#,
        )
        .bind(reason)
        .bind(undo_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Mark the corresponding effect as compensation_failed.
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn mark_effect_compensation_failed(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_effect_log
             SET state = 'compensation_failed'
             WHERE effect_id = $1 AND org_id = $2"#,
        )
        .bind(effect_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

// ── Row mappers ──────────────────────────────────────────────────────────────

fn map_effect_row(row: sqlx::sqlite::SqliteRow) -> UndoLogResult<EffectRecord> {
    use sqlx::Row;

    let effect_id_str: String = row.try_get("effect_id")?;
    let org_id_str: String = row.try_get("org_id")?;
    let session_id_str: String = row.try_get("session_id")?;
    let tool_id_str: Option<String> = row.try_get("tool_id")?;
    let approval_str: Option<String> = row.try_get("approval_request_id")?;
    let state_str: String = row.try_get("state")?;
    let result_json: Option<String> = row.try_get("result_snapshot")?;

    let effect_id: uuid::Uuid = effect_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid effect_id: {e}")))?;
    let org_id: uuid::Uuid =
        org_id_str.parse().map_err(|e| UndoLogError::Internal(format!("Invalid org_id: {e}")))?;
    let session_id: uuid::Uuid = session_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid session_id: {e}")))?;

    let state = parse_effect_state(&state_str)?;
    let result = result_json
        .map(|s| serde_json::from_str::<ToolResult>(&s))
        .transpose()
        .map_err(UndoLogError::Serialization)?;

    let tier_str: String = row.try_get("tier")?;

    let args_snapshot_str: String = row.try_get("args_snapshot")?;
    let args_snapshot: serde_json::Value = serde_json::from_str(&args_snapshot_str)
        .map_err(|e| UndoLogError::Internal(format!("Invalid args_snapshot JSON: {e}")))?;

    Ok(EffectRecord {
        effect_id: EffectId::from(effect_id),
        org_id: OrgId::from(org_id),
        session_id: SessionId::from(session_id),
        tool_id: tool_id_str.and_then(|s| s.parse::<uuid::Uuid>().ok()).map(ToolId::from),
        call_signature: CallSignature(row.try_get("call_signature")?),
        tool_name: row.try_get("tool_name")?,
        tool_version: row.try_get("tool_version")?,
        tier: parse_tool_tier(&tier_str),
        step_index: row.try_get::<i64, _>("step_index")? as u32,
        args_snapshot,
        result_snapshot: result,
        state,
        compensation_args: serde_json::from_str(&row.try_get::<String, _>("compensation_args")?)
            .ok(),
        executed_at: parse_timestamp(&row.try_get::<String, _>("executed_at")?),
        committed_at: row
            .try_get::<Option<String>, _>("committed_at")?
            .map(|s| parse_timestamp(&s)),
        compensated_at: row
            .try_get::<Option<String>, _>("compensated_at")?
            .map(|s| parse_timestamp(&s)),
        replay_count: row.try_get::<i64, _>("replay_count")? as u16,
        last_replayed_at: row
            .try_get::<Option<String>, _>("last_replayed_at")?
            .map(|s| parse_timestamp(&s)),
        approval_request_id: approval_str
            .and_then(|s| s.parse::<uuid::Uuid>().ok())
            .map(ApprovalRequestId::from),
    })
}

fn map_undo_row(row: sqlx::sqlite::SqliteRow) -> UndoLogResult<UndoEntry> {
    use sqlx::Row;

    let undo_id_str: String = row.try_get("undo_id")?;
    let org_id_str: String = row.try_get("org_id")?;
    let session_id_str: String = row.try_get("session_id")?;
    let effect_id_str: String = row.try_get("effect_id")?;

    let undo_id: uuid::Uuid =
        undo_id_str.parse().map_err(|e| UndoLogError::Internal(format!("Invalid undo_id: {e}")))?;
    let org_id: uuid::Uuid =
        org_id_str.parse().map_err(|e| UndoLogError::Internal(format!("Invalid org_id: {e}")))?;
    let session_id: uuid::Uuid = session_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid session_id: {e}")))?;
    let effect_id: uuid::Uuid = effect_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid effect_id: {e}")))?;

    let comp_args_str: String = row.try_get("compensation_args")?;
    let comp_args: serde_json::Value =
        serde_json::from_str(&comp_args_str).unwrap_or(serde_json::Value::Null);
    let state_str: String = row.try_get("state")?;

    Ok(UndoEntry {
        undo_id: UndoId::from(undo_id),
        org_id: OrgId::from(org_id),
        session_id: SessionId::from(session_id),
        effect_id: EffectId::from(effect_id),
        stack_position: row.try_get::<i64, _>("stack_position")? as u32,
        compensation: CompensationDescriptor {
            fn_name: row.try_get("compensation_fn")?,
            fn_version: row.try_get("compensation_version")?,
            args: comp_args,
            max_retries: row.try_get::<i64, _>("max_retries")? as u8,
            retry_backoff_ms: row.try_get::<i64, _>("retry_backoff_ms")? as u32,
        },
        state: parse_saga_state(&state_str),
        retry_count: row.try_get::<i64, _>("retry_count")? as u8,
        last_error: row.try_get("last_error")?,
        registered_at: parse_timestamp(&row.try_get::<String, _>("registered_at")?),
        compensated_at: row
            .try_get::<Option<String>, _>("compensated_at")?
            .map(|s| parse_timestamp(&s)),
    })
}

fn parse_effect_state(s: &str) -> UndoLogResult<EffectState> {
    match s {
        "pending" => Ok(EffectState::Pending),
        "executing" => Ok(EffectState::Executing),
        "committed" => Ok(EffectState::Committed),
        "compensating" => Ok(EffectState::Compensating),
        "compensated" => Ok(EffectState::Compensated),
        "compensation_failed" => Ok(EffectState::CompensationFailed),
        "approved" => Ok(EffectState::Approved),
        "rejected" => Ok(EffectState::Rejected),
        "replayed" => Ok(EffectState::Replayed),
        other => Err(UndoLogError::Internal(format!("Unknown effect state: {other}"))),
    }
}

fn parse_tool_tier(s: &str) -> ToolTier {
    match s {
        "safe" => ToolTier::Safe,
        "compensable" => ToolTier::Compensable {
            compensation: CompensationDescriptor {
                fn_name: String::new(),
                fn_version: String::new(),
                args: serde_json::Value::Null,
                max_retries: 0,
                retry_backoff_ms: 0,
            },
        },
        "irreversible" => ToolTier::Irreversible { reason: String::new() },
        _ => ToolTier::Safe,
    }
}

fn parse_saga_state(s: &str) -> SagaStepState {
    match s {
        "running" => SagaStepState::Running,
        "compensated" => SagaStepState::Compensated,
        "failed" => SagaStepState::Failed,
        "skipped" => SagaStepState::Skipped,
        _ => SagaStepState::Pending,
    }
}
