//! SQLite SessionStore: session CRUD operations.

use sqlx::sqlite::{SqliteConnection, SqlitePool};
use sqlx::Row;
use tracing::instrument;

use undolog_types::{
    errors::{UndoLogError, UndoLogResult},
    ids::{OrgId, ProjectId, SessionId},
    session::{SessionRecord, SessionState},
};

use super::util::parse_timestamp;

/// Repository for sessions (SQLite backend).
#[derive(Clone)]
pub struct SessionStore {
    pool: SqlitePool,
}

impl SessionStore {
    /// Create a new session repository over the given SQLite pool.
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Create a new session (auto-created on first tool intercept).
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn create_session(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"INSERT OR IGNORE INTO undolog_sessions (session_id, org_id, state, started_at)
             VALUES ($1, $2, 'active', datetime('now'))"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Load one session row by ID.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn get_session(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<Option<SessionRecord>> {
        let row = sqlx::query(
            r#"SELECT
                session_id, org_id, project_id, external_run_id, agent_name,
                state, tool_calls_total, compensations_total, approvals_pending,
                started_at, completed_at, failed_at, failure_reason, metadata
             FROM undolog_sessions
             WHERE session_id = $1 AND org_id = $2
             LIMIT 1"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .fetch_optional(&self.pool)
        .await?;

        row.map(map_session_row).transpose()
    }

    /// Transition a session to `awaiting_approval`.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_awaiting_approval(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'awaiting_approval'
             WHERE session_id = $1 AND org_id = $2 AND state = 'active'"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition a session back to `active` after an approval resumes it.
    #[instrument(skip(self, conn), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_active(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        let rows = sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'active'
             WHERE session_id = $1 AND org_id = $2 AND state = 'awaiting_approval'"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(conn)
        .await?
        .rows_affected();

        if rows == 0 {
            return Err(UndoLogError::InvalidStateTransition {
                effect_id: session_id.to_string(),
                current_state: "not awaiting_approval".to_string(),
                target_state: "active".to_string(),
            });
        }
        Ok(())
    }

    /// Transition to `compensating`.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_compensating(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'compensating'
             WHERE session_id = $1 AND org_id = $2
               AND state IN ('active', 'awaiting_approval', 'failed')"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition to `compensated`.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_compensated(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'compensated'
             WHERE session_id = $1 AND org_id = $2
               AND state IN ('active', 'failed', 'compensating')"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Transition to `halted` using a provided DB connection (for transactional use).
    #[instrument(skip(self, conn), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_halted_with_conn(
        &self,
        conn: &mut SqliteConnection,
        org_id: &OrgId,
        session_id: &SessionId,
        reason: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'halted', failure_reason = $1
             WHERE session_id = $2 AND org_id = $3"#,
        )
        .bind(reason)
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(conn)
        .await?;
        Ok(())
    }

    /// Transition to `halted` - a compensation failed permanently.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_halted(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
        reason: &str,
    ) -> UndoLogResult<()> {
        let mut conn = self.pool.acquire().await?;
        self.set_halted_with_conn(&mut conn, org_id, session_id, reason).await
    }

    /// Mark session as `failed`.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_failed(
        &self,
        org_id: &OrgId,
        session_id: &SessionId,
        reason: &str,
    ) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'failed', failed_at = datetime('now'), failure_reason = $1
             WHERE session_id = $2 AND org_id = $3 AND state = 'active'"#,
        )
        .bind(reason)
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Mark session as `completed`.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn set_completed(&self, org_id: &OrgId, session_id: &SessionId) -> UndoLogResult<()> {
        sqlx::query(
            r#"UPDATE undolog_sessions
             SET state = 'completed', completed_at = datetime('now')
             WHERE session_id = $1 AND org_id = $2 AND state = 'active'"#,
        )
        .bind(session_id.to_string())
        .bind(org_id.to_string())
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

fn map_session_row(row: sqlx::sqlite::SqliteRow) -> UndoLogResult<SessionRecord> {
    let session_id_str: String = row.try_get("session_id")?;
    let org_id_str: String = row.try_get("org_id")?;
    let state_str: String = row.try_get("state")?;

    let session_id: uuid::Uuid = session_id_str
        .parse()
        .map_err(|e| UndoLogError::Internal(format!("Invalid session_id: {e}")))?;
    let org_id: uuid::Uuid =
        org_id_str.parse().map_err(|e| UndoLogError::Internal(format!("Invalid org_id: {e}")))?;

    let project_id_str: Option<String> = row.try_get("project_id")?;
    let project_id = project_id_str.and_then(|s| s.parse::<uuid::Uuid>().ok()).map(ProjectId::from);

    let state = parse_session_state(&state_str)?;

    let metadata_str: String = row.try_get("metadata")?;
    let metadata: serde_json::Value = serde_json::from_str(&metadata_str)
        .unwrap_or(serde_json::Value::Object(serde_json::Map::new()));

    Ok(SessionRecord {
        session_id: SessionId::from(session_id),
        org_id: OrgId::from(org_id),
        project_id,
        external_run_id: row.try_get("external_run_id")?,
        agent_name: row.try_get("agent_name")?,
        state,
        tool_calls_total: row.try_get::<i64, _>("tool_calls_total")? as u32,
        compensations_total: row.try_get::<i64, _>("compensations_total")? as u32,
        approvals_pending: row.try_get::<i64, _>("approvals_pending")? as u32,
        started_at: parse_timestamp(&row.try_get::<String, _>("started_at")?),
        completed_at: row
            .try_get::<Option<String>, _>("completed_at")?
            .map(|s| parse_timestamp(&s)),
        failed_at: row.try_get::<Option<String>, _>("failed_at")?.map(|s| parse_timestamp(&s)),
        failure_reason: row.try_get("failure_reason")?,
        metadata,
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
