//! SessionStore - repository for `undolog_sessions`.

use sqlx::PgPool;
use undolog_types::{
    errors::{UndoLogError, UndoLogResult},
    ids::{OrgId, SessionId},
    session::{SessionRecord, SessionState},
};

/// Repository for `undolog_sessions`.
#[derive(Clone)]
pub struct SessionStore {
    pool: PgPool,
}

impl SessionStore {
    /// Create a new session repository over the given PostgreSQL pool.
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// Create a new session (auto-created on first tool intercept).
    pub async fn create_session(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            INSERT INTO undolog_sessions (session_id, org_id, state, started_at)
            VALUES ($1, $2, 'active'::undolog_session_state, now())
            ON CONFLICT (session_id) DO NOTHING
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Load one session row by ID.
    ///
    /// Returns `Ok(None)` when the session does not exist.
    pub async fn get_session(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<Option<SessionRecord>> {
        let row = sqlx::query(
            r#"
            SELECT
                session_id, org_id, project_id, external_run_id, agent_name,
                state::text, tool_calls_total, compensations_total, approvals_pending,
                started_at, completed_at, failed_at, failure_reason, metadata
            FROM undolog_sessions
            WHERE session_id = $1
              AND org_id     = $2
            LIMIT 1
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .fetch_optional(&self.pool)
        .await?;

        row.map(map_session_row).transpose()
    }

    /// Transition a session to `awaiting_approval`.
    pub async fn set_awaiting_approval(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state = 'awaiting_approval'::undolog_session_state
            WHERE session_id = $1
              AND org_id     = $2
              AND state      = 'active'::undolog_session_state
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition a session back to `active` after an approval resumes it.
    pub async fn set_active(&self, org_id: &OrgId, session_id: &SessionId) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state = 'active'::undolog_session_state
            WHERE session_id = $1
              AND org_id     = $2
              AND state      = 'awaiting_approval'::undolog_session_state
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition to `compensating` - Saga Orchestrator is walking the undo stack.
    pub async fn set_compensating(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state = 'compensating'::undolog_session_state
            WHERE session_id = $1
              AND org_id     = $2
              AND state IN (
                  'active'::undolog_session_state,
                  'awaiting_approval'::undolog_session_state,
                  'failed'::undolog_session_state
              )
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition to `compensated` - all undo steps completed.
    pub async fn set_compensated(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state = 'compensated'::undolog_session_state
            WHERE session_id = $1
              AND org_id     = $2
              AND state IN (
                  'active'::undolog_session_state,
                  'failed'::undolog_session_state,
                  'compensating'::undolog_session_state
              )
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition to `halted` - a compensation failed permanently.
    pub async fn set_halted(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
        reason: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state          = 'halted'::undolog_session_state,
                failure_reason = $1
            WHERE session_id = $2
              AND org_id     = $3
            "#,
        )
        .bind(reason)
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Mark session as `failed` (tool returned an error before compensation begins).
    pub async fn set_failed(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
        reason: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state          = 'failed'::undolog_session_state,
                failed_at      = now(),
                failure_reason = $1
            WHERE session_id = $2
              AND org_id     = $3
              AND state      = 'active'::undolog_session_state
            "#,
        )
        .bind(reason)
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Mark session as `completed` successfully.
    pub async fn set_completed(&self, org_id: &OrgId, session_id: &SessionId) -> UndoLogResult<()> {
        sqlx::query(
            r#"
            UPDATE undolog_sessions
            SET state        = 'completed'::undolog_session_state,
                completed_at = now()
            WHERE session_id = $1
              AND org_id     = $2
              AND state      = 'active'::undolog_session_state
            "#,
        )
        .bind(*session_id.as_uuid())
        .bind(*org_id.as_uuid())
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

fn map_session_row(row: sqlx::postgres::PgRow) -> UndoLogResult<SessionRecord> {
    use sqlx::Row;
    use uuid::Uuid;

    let session_id_uuid: Uuid = row.try_get("session_id")?;
    let org_id_uuid: Uuid = row.try_get("org_id")?;
    let state_str: String = row.try_get("state")?;

    Ok(SessionRecord {
        session_id: SessionId::from(session_id_uuid),
        org_id: OrgId::from(org_id_uuid),
        project_id: row
            .try_get::<Option<Uuid>, _>("project_id")?
            .map(undolog_types::ids::ProjectId::from),
        external_run_id: row.try_get("external_run_id")?,
        agent_name: row.try_get("agent_name")?,
        state: parse_session_state(&state_str)?,
        tool_calls_total: row.try_get::<i32, _>("tool_calls_total")? as u32,
        compensations_total: row.try_get::<i32, _>("compensations_total")? as u32,
        approvals_pending: row.try_get::<i32, _>("approvals_pending")? as u32,
        started_at: row.try_get("started_at")?,
        completed_at: row.try_get("completed_at")?,
        failed_at: row.try_get("failed_at")?,
        failure_reason: row.try_get("failure_reason")?,
        metadata: row.try_get("metadata")?,
    })
}

fn parse_session_state(state: &str) -> UndoLogResult<SessionState> {
    match state {
        "active" => Ok(SessionState::Active),
        "completed" => Ok(SessionState::Completed),
        "failed" => Ok(SessionState::Failed),
        "compensating" => Ok(SessionState::Compensating),
        "compensated" => Ok(SessionState::Compensated),
        "awaiting_approval" => Ok(SessionState::AwaitingApproval),
        "halted" => Ok(SessionState::Halted),
        other => Err(UndoLogError::Internal(format!("unknown session state: {other}"))),
    }
}
