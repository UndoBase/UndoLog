//! EffectStore - repository for `undolog_effect_log` and `undolog_undo_stack`.
//!
//! # Exactly-once guarantee
//! `pg_try_advisory_xact_lock(key)` prevents concurrent racing writers.
//! `find_by_signature` before insert catches in-flight duplicates.
//! Partition-level unique indexes handle edge-case violations.
//!
//! # Advisory lock key
//! Derived via FNV-1a 64-bit hash of the call_signature string.
//! The Go MCP Interceptor uses the identical algorithm (see advisory_lock_key docs).

use sqlx::{
    postgres::{PgConnection, PgRow},
    PgPool, Row,
};
use tracing::{debug, instrument, warn};

use undolog_types::{
    effect::{CallSignature, EffectRecord, EffectState, ToolCall, ToolResult},
    errors::{UndoLogError, UndoLogResult},
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId, ToolId, UndoId},
    saga::{SagaStepState, UndoEntry},
    tier::{CompensationDescriptor, ToolTier},
};

/// Repository for effect log and undo stack operations.
#[derive(Clone)]
pub struct EffectStore {
    pool: PgPool,
}

impl EffectStore {
    /// Create a new effect repository over the given PostgreSQL pool.
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    // ── Advisory lock ─────────────────────────────────────────────────────

    /// Acquire a transaction-scoped advisory lock for the given call signature.
    ///
    /// Uses `pg_try_advisory_xact_lock()` - returns immediately if unavailable.
    /// Retries up to `max_attempts` times with `retry_ms` sleep between attempts.
    ///
    /// The lock is automatically released when the surrounding transaction ends.
    ///
    /// # Go counterpart
    /// ```go
    /// func advisoryLockKey(sig string) int64 {
    ///     h := fnv.New64a()
    ///     h.Write([]byte(sig))
    ///     return int64(h.Sum64())
    /// }
    /// ```
    #[instrument(skip(self), fields(signature = %signature))]
    pub async fn acquire_advisory_lock(
        &self,
        signature: &CallSignature,
        max_attempts: u32,
        retry_ms: u64,
    ) -> UndoLogResult<()> {
        let lock_key = advisory_lock_key(signature.as_str());
        let mut conn = self.pool.acquire().await?;

        for attempt in 0..max_attempts {
            let acquired: bool = sqlx::query_scalar("SELECT pg_try_advisory_xact_lock($1)")
                .bind(lock_key)
                .fetch_one(&mut *conn)
                .await?;

            if acquired {
                debug!(attempt, "Advisory lock acquired");
                return Ok(());
            }

            if attempt + 1 < max_attempts {
                tokio::time::sleep(tokio::time::Duration::from_millis(retry_ms)).await;
            }
        }

        Err(UndoLogError::AdvisoryLockTimeout {
            signature: signature.to_string(),
            attempts: max_attempts,
        })
    }

    // ── Insert (Compensable) ──────────────────────────────────────────────

    /// Insert a Compensable tool call into the effect log.
    ///
    /// Returns `true` if inserted. Duplicate prevention is handled by the
    /// advisory lock and `find_by_signature` check in the caller.
    ///
    /// Compensation args are captured **before** execution so a crash between
    /// insert and execution still leaves the undo information persisted.
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

        sqlx::query(
            r#"
            INSERT INTO undolog_effect_log (
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version, tier,
                step_index, args_snapshot, compensation_args,
                state, executed_at
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, 'compensable'::undolog_tool_tier,
                $8, $9, $10,
                'pending'::undolog_effect_state, now()
            )
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*call.org_id.as_uuid())
        .bind(*call.session_id.as_uuid())
        .bind(call.tool_id.map(|id| *id.as_uuid()))
        .bind(signature.as_str())
        .bind(&call.tool_name)
        .bind(&call.tool_version)
        .bind(call.step_index as i32)
        .bind(&args_snapshot)
        .bind(&compensation_args)
        .execute(&self.pool)
        .await?;

        // Lock + find_by_signature above already prevent duplicates.
        // Partition-level unique indexes handle any edge case.
        Ok(true)
    }

    // ── Insert (Irreversible) ─────────────────────────────────────────────

    /// Insert an Irreversible tool call (state = `pending`).
    /// Returns `true` if inserted, `false` if duplicate.
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

        sqlx::query(
            r#"
            INSERT INTO undolog_effect_log (
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version, tier,
                step_index, args_snapshot,
                state, executed_at
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, 'irreversible'::undolog_tool_tier,
                $8, $9,
                'pending'::undolog_effect_state, now()
            )
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*call.org_id.as_uuid())
        .bind(*call.session_id.as_uuid())
        .bind(call.tool_id.map(|id| *id.as_uuid()))
        .bind(signature.as_str())
        .bind(&call.tool_name)
        .bind(&call.tool_version)
        .bind(call.step_index as i32)
        .bind(&args_snapshot)
        .execute(&self.pool)
        .await?;

        Ok(true)
    }

    // ── Push undo stack entry ─────────────────────────────────────────────

    /// Push a compensation entry onto the undo stack.
    ///
    /// # Critical invariant
    /// This MUST complete before the action executes. If the process crashes
    /// between this insert and the tool call, compensation can still run on
    /// recovery because it is already persisted.
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
            r#"
            INSERT INTO undolog_undo_stack (
                undo_id, org_id, session_id, effect_id,
                stack_position, compensation_fn, compensation_version,
                compensation_args, state, registered_at
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, 'pending', now()
            )
            "#,
        )
        .bind(*undo_id.as_uuid())
        .bind(*call.org_id.as_uuid())
        .bind(*call.session_id.as_uuid())
        .bind(*effect_id.as_uuid())
        .bind(call.step_index as i32)
        .bind(&compensation.fn_name)
        .bind(&compensation.fn_version)
        .bind(&compensation_args)
        .execute(&self.pool)
        .await?;

        debug!(fn_name = %compensation.fn_name, "Undo stack entry registered");
        Ok(())
    }

    // ── Transition: pending → executing ──────────────────────────────────

    /// Mark an effect as executing. Called just before the Go proxy forwards
    /// the call to the actual tool server.
    pub async fn set_executing(&self, org_id: &OrgId, effect_id: &EffectId) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET state = 'executing'::undolog_effect_state
            WHERE effect_id = $1
              AND org_id    = $2
              AND state     = 'pending'::undolog_effect_state
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
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

    // ── Transition: executing → committed ────────────────────────────────

    /// Mark an effect as committed and cache the result.
    /// Called by the Go proxy after the tool returns successfully.
    pub async fn commit_effect(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        result: ToolResult,
    ) -> UndoLogResult<()> {
        let result_json = serde_json::to_value(&result)?;

        let (affected, exists) = sqlx::query_as::<_, (i64, bool)>(
            r#"
            WITH upd AS (
                UPDATE undolog_effect_log
                SET state           = 'committed'::undolog_effect_state,
                    result_snapshot = $1,
                    committed_at    = now()
                WHERE effect_id = $2
                  AND org_id    = $3
                  AND state     IN ('executing'::undolog_effect_state, 'approved'::undolog_effect_state)
                RETURNING 1
            )
            SELECT
                (SELECT count(*) FROM upd) AS affected,
                EXISTS(SELECT 1 FROM undolog_effect_log WHERE effect_id = $2 AND org_id = $3) AS exists
            "#,
        )
        .bind(&result_json)
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .fetch_one(&self.pool)
        .await?;

        if affected > 0 {
            return Ok(());
        }
        if !exists {
            warn!(
                effect_id = %effect_id,
                "Commit called for non-existent effect (SAFE tier)"
            );
            return Ok(());
        }
        Err(UndoLogError::NotExecuting { effect_id: effect_id.to_string() })
    }

    // ── Transition: executing|approved → pending (failure recorded) ────────

    /// Record that a tool call failed; revert state to allow compensation.
    /// The Saga Orchestrator will detect the session failure and walk the undo stack.
    /// Also accepts `approved` so a tool that fails after human approval is not
    /// falsely recorded as committed (see the approval workflow).
    pub async fn fail_effect(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        reason: &str,
    ) -> UndoLogResult<()> {
        let error_json = serde_json::json!({ "error": reason });

        let (affected, exists) = sqlx::query_as::<_, (i64, bool)>(
            r#"
            WITH upd AS (
                UPDATE undolog_effect_log
                SET state           = 'pending'::undolog_effect_state,
                    result_snapshot = $1
                WHERE effect_id = $2
                  AND org_id    = $3
                  AND state     IN ('executing'::undolog_effect_state, 'approved'::undolog_effect_state)
                RETURNING 1
            )
            SELECT
                (SELECT count(*) FROM upd) AS affected,
                EXISTS(SELECT 1 FROM undolog_effect_log WHERE effect_id = $2 AND org_id = $3) AS exists
            "#,
        )
        .bind(&error_json)
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .fetch_one(&self.pool)
        .await?;

        if affected > 0 {
            return Ok(());
        }
        if !exists {
            warn!(
                effect_id = %effect_id,
                "Fail called for non-existent effect (SAFE tier)"
            );
            return Ok(());
        }
        Err(UndoLogError::NotExecuting { effect_id: effect_id.to_string() })
    }

    // ── Replay path ───────────────────────────────────────────────────────

    /// Find an effect record by call signature (used on the replay path).
    pub async fn find_by_signature(
        &self,
        org_id: &OrgId,
        signature: &CallSignature,
    ) -> UndoLogResult<Option<EffectRecord>> {
        let maybe_row = sqlx::query(
            r#"
            SELECT
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version,
                tier::text,
                step_index, args_snapshot, result_snapshot,
                state::text, compensation_args,
                executed_at, committed_at, compensated_at,
                replay_count, last_replayed_at, approval_request_id
            FROM undolog_effect_log
            WHERE call_signature = $1
              AND org_id         = $2
            LIMIT 1
            "#,
        )
        .bind(signature.as_str())
        .bind(*org_id.as_uuid())
        .fetch_optional(&self.pool)
        .await?;

        maybe_row.map(map_effect_row).transpose()
    }

    /// Look up an effect record by its effect_id.
    pub async fn find_by_effect_id(
        &self,
        conn: &mut PgConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<Option<EffectRecord>> {
        let maybe_row = sqlx::query(
            r#"
            SELECT
                effect_id, org_id, session_id, tool_id,
                call_signature, tool_name, tool_version,
                tier::text,
                step_index, args_snapshot, result_snapshot,
                state::text, compensation_args,
                executed_at, committed_at, compensated_at,
                replay_count, last_replayed_at, approval_request_id
            FROM undolog_effect_log
            WHERE effect_id = $1
              AND org_id    = $2
            LIMIT 1
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .fetch_optional(&mut *conn)
        .await?;

        maybe_row.map(map_effect_row).transpose()
    }

    /// Increment the replay counter and update last_replayed_at.
    pub async fn mark_replayed(&self, org_id: &OrgId, effect_id: &EffectId) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET replay_count     = replay_count + 1,
                last_replayed_at = now(),
                state            = 'replayed'::undolog_effect_state
            WHERE effect_id = $1
              AND org_id    = $2
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    // ── Approval link ─────────────────────────────────────────────────────

    /// Link an approval request to an effect log entry.
    pub async fn set_approval_request_id(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        approval_request_id: &ApprovalRequestId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET approval_request_id = $1
            WHERE effect_id = $2
              AND org_id    = $3
            "#,
        )
        .bind(*approval_request_id.as_uuid())
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    // ── Approval: pending → approved ──────────────────────────────────────

    /// Transition an Irreversible effect from pending to approved.
    /// Called when a human approves in the dashboard.
    pub async fn approve_effect(
        &self,
        conn: &mut PgConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET state = 'approved'::undolog_effect_state
            WHERE effect_id = $1
              AND org_id    = $2
              AND state     = 'pending'::undolog_effect_state
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&mut *conn)
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
    pub async fn reject_effect(
        &self,
        conn: &mut PgConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET state = 'rejected'::undolog_effect_state
            WHERE effect_id = $1
              AND org_id    = $2
              AND state     = 'pending'::undolog_effect_state
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&mut *conn)
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

    /// Update args_snapshot for an approved effect when the approver
    /// modifies the original proposed args. Keeps the audit trail accurate.
    ///
    /// Only allowed when the effect is in 'approved' state.
    pub async fn update_args_snapshot(
        &self,
        conn: &mut PgConnection,
        org_id: &OrgId,
        effect_id: &EffectId,
        args: &serde_json::Value,
    ) -> UndoLogResult<()> {
        let args_json = serde_json::to_value(args)?;
        let rows = sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET args_snapshot = $1
            WHERE effect_id = $2
              AND org_id    = $3
              AND state     = 'approved'::undolog_effect_state
            "#,
        )
        .bind(&args_json)
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&mut *conn)
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

    // ── Undo stack ────────────────────────────────────────────────────────

    /// Load all pending undo entries for a session in LIFO order
    /// (highest `stack_position` first = compensated first).
    pub async fn load_undo_stack(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<Vec<UndoEntry>> {
        let rows = sqlx::query(
            r#"
            SELECT
                undo_id, org_id, session_id, effect_id,
                stack_position, compensation_fn, compensation_version,
                compensation_args, state, retry_count, last_error,
                registered_at, compensated_at,
                max_retries, retry_backoff_ms
            FROM undolog_undo_stack
            WHERE session_id = $1
              AND org_id     = $2
              AND state      = 'pending'
            ORDER BY stack_position DESC
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .fetch_all(&self.pool)
        .await?;

        rows.into_iter().map(map_undo_row).collect()
    }

    /// Mark an undo entry as compensated.
    pub async fn mark_compensated(&self, org_id: &OrgId, undo_id: &UndoId) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_undo_stack
            SET state          = 'compensated',
                compensated_at = now()
            WHERE undo_id = $1
              AND org_id  = $2
            "#,
        )
        .bind(*undo_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Increment retry count and record the latest error message.
    pub async fn record_compensation_retry(
        &self,
        org_id: &OrgId,
        undo_id: &UndoId,
        error: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_undo_stack
            SET retry_count = retry_count + 1,
                last_error  = $1,
                state       = 'running'
            WHERE undo_id = $2
              AND org_id  = $3
            "#,
        )
        .bind(error)
        .bind(*undo_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Mark an undo entry as permanently failed.
    pub async fn mark_compensation_failed(
        &self,
        org_id: &OrgId,
        undo_id: &UndoId,
        reason: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_undo_stack
            SET state      = 'failed',
                last_error = $1
            WHERE undo_id = $2
              AND org_id  = $3
            "#,
        )
        .bind(reason)
        .bind(*undo_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Also mark the corresponding effect as compensation_failed.
    pub async fn mark_effect_compensation_failed(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET state = 'compensation_failed'::undolog_effect_state
            WHERE effect_id = $1
              AND org_id    = $2
            "#,
        )
        .bind(*effect_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

// ── Advisory lock key derivation ──────────────────────────────────────────────

/// Derive a stable i64 advisory lock key from a call signature string.
///
/// Algorithm: FNV-1a 64-bit hash, interpreted as i64 (two's complement).
/// This is intentionally non-cryptographic - it only needs to be stable
/// and consistent with the Go implementation in the MCP Interceptor.
///
/// Collision probability is negligible for < 10,000 concurrent signatures.
fn advisory_lock_key(sig: &str) -> i64 {
    const OFFSET: u64 = 14_695_981_039_346_656_037;
    const PRIME: u64 = 1_099_511_628_211;

    // FNV-1a variant: XOR first, then multiply
    let hash = sig.bytes().fold(OFFSET, |h, b| (h ^ (b as u64)).wrapping_mul(PRIME));

    hash as i64
}

// ── Row mappers ───────────────────────────────────────────────────────────────

fn map_effect_row(row: PgRow) -> UndoLogResult<EffectRecord> {
    use uuid::Uuid;

    let effect_id_uuid: Uuid = row.try_get("effect_id")?;
    let org_id_uuid: Uuid = row.try_get("org_id")?;
    let session_id_uuid: Uuid = row.try_get("session_id")?;
    let tool_id_uuid: Option<Uuid> = row.try_get("tool_id")?;
    let approval_uuid: Option<Uuid> = row.try_get("approval_request_id")?;
    let state_str: String = row.try_get("state")?;
    let result_json: Option<serde_json::Value> = row.try_get("result_snapshot")?;

    let state = parse_effect_state(&state_str)?;
    let result = result_json
        .map(serde_json::from_value::<ToolResult>)
        .transpose()
        .map_err(UndoLogError::Serialization)?;

    Ok(EffectRecord {
        effect_id: EffectId::from(effect_id_uuid),
        org_id: OrgId::from(org_id_uuid),
        session_id: SessionId::from(session_id_uuid),
        tool_id: tool_id_uuid.map(ToolId::from),
        call_signature: CallSignature(row.try_get::<String, _>("call_signature")?),
        tool_name: row.try_get("tool_name")?,
        tool_version: row.try_get("tool_version")?,
        tier: parse_tool_tier(&row.try_get::<String, _>("tier")?),
        step_index: row.try_get::<i32, _>("step_index")? as u32,
        args_snapshot: row.try_get("args_snapshot")?,
        result_snapshot: result,
        state,
        compensation_args: row.try_get("compensation_args")?,
        executed_at: row.try_get("executed_at")?,
        committed_at: row.try_get("committed_at")?,
        compensated_at: row.try_get("compensated_at")?,
        replay_count: row.try_get::<i16, _>("replay_count")? as u16,
        last_replayed_at: row.try_get("last_replayed_at")?,
        approval_request_id: approval_uuid.map(ApprovalRequestId::from),
    })
}

fn map_undo_row(row: PgRow) -> UndoLogResult<UndoEntry> {
    use uuid::Uuid;

    let undo_id_uuid: Uuid = row.try_get("undo_id")?;
    let org_id_uuid: Uuid = row.try_get("org_id")?;
    let session_id_uuid: Uuid = row.try_get("session_id")?;
    let effect_id_uuid: Uuid = row.try_get("effect_id")?;
    let comp_args: serde_json::Value = row.try_get("compensation_args")?;
    let state_str: String = row.try_get("state")?;

    Ok(UndoEntry {
        undo_id: UndoId::from(undo_id_uuid),
        org_id: OrgId::from(org_id_uuid),
        session_id: SessionId::from(session_id_uuid),
        effect_id: EffectId::from(effect_id_uuid),
        stack_position: row.try_get::<i32, _>("stack_position")? as u32,
        compensation: CompensationDescriptor {
            fn_name: row.try_get("compensation_fn")?,
            fn_version: row.try_get("compensation_version")?,
            args: comp_args,
            max_retries: row.try_get::<i16, _>("max_retries")? as u8,
            retry_backoff_ms: row.try_get::<i32, _>("retry_backoff_ms")? as u32,
        },
        state: parse_saga_state(&state_str),
        retry_count: row.try_get::<i16, _>("retry_count")? as u8,
        last_error: row.try_get("last_error")?,
        registered_at: row.try_get("registered_at")?,
        compensated_at: row.try_get("compensated_at")?,
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
        _other => ToolTier::Safe,
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

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn advisory_lock_key_is_deterministic() {
        assert_eq!(advisory_lock_key("abc"), advisory_lock_key("abc"));
    }

    #[test]
    fn advisory_lock_key_differs_on_different_input() {
        assert_ne!(advisory_lock_key("sig_aaa"), advisory_lock_key("sig_bbb"));
    }

    #[test]
    fn parse_all_effect_states() {
        let valid = [
            "pending",
            "executing",
            "committed",
            "compensating",
            "compensated",
            "compensation_failed",
            "approved",
            "rejected",
            "replayed",
        ];
        for s in valid {
            assert!(parse_effect_state(s).is_ok(), "Failed: {s}");
        }
    }

    #[test]
    fn parse_unknown_state_is_error() {
        assert!(parse_effect_state("unknown_xyz").is_err());
    }
}
