//! Dead-letter store for failed compensations.
//!
//! When a compensation exhausts its retries, the effect is moved to the
//! dead-letter table for inspection, retry, or skip operations.

use chrono::{DateTime, Utc};
use sqlx::{PgPool, Row};
use tracing::{debug, instrument};

use undolog_types::{
    errors::UndoLogResult,
    ids::{EffectId, OrgId, SessionId, UndoId},
};

/// State of a dead-letter record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeadLetterState {
    /// Compensation failed and is awaiting action.
    Failed,
    /// Retry is in progress.
    Retrying,
    /// Skipped by admin action.
    Skipped,
}

impl DeadLetterState {
    /// Convert to SQL string representation.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Failed => "failed",
            Self::Retrying => "retrying",
            Self::Skipped => "skipped",
        }
    }

    /// Parse from SQL string representation.
    pub fn parse_str(s: &str) -> Option<Self> {
        match s {
            "failed" => Some(Self::Failed),
            "retrying" => Some(Self::Retrying),
            "skipped" => Some(Self::Skipped),
            _ => None,
        }
    }
}

/// A dead-letter record for a failed compensation.
#[derive(Debug, Clone)]
pub struct DeadLetterRecord {
    /// Unique identifier for this dead-letter record.
    pub dead_letter_id: uuid::Uuid,
    /// Organization that owns this record.
    pub org_id: OrgId,
    /// Session that produced the failed compensation.
    pub session_id: SessionId,
    /// Effect that failed to compensate.
    pub effect_id: EffectId,
    /// Undo entry that failed.
    pub undo_id: UndoId,
    /// Compensation function name.
    pub compensation_fn: String,
    /// Compensation function version.
    pub compensation_version: String,
    /// Compensation arguments as JSON.
    pub compensation_args: serde_json::Value,
    /// Error message from the failed compensation.
    pub error_message: String,
    /// Number of retry attempts.
    pub retry_count: i32,
    /// Current state of the dead-letter record.
    pub state: DeadLetterState,
    /// When this record was created.
    pub created_at: DateTime<Utc>,
    /// When this record was last updated.
    pub updated_at: DateTime<Utc>,
}

/// Parameters for creating a dead-letter record.
#[derive(Debug, Clone)]
pub struct CreateDeadLetterParams {
    /// Organization that owns this record.
    pub org_id: OrgId,
    /// Session that produced the failed compensation.
    pub session_id: SessionId,
    /// Effect that failed to compensate.
    pub effect_id: EffectId,
    /// Undo entry that failed.
    pub undo_id: UndoId,
    /// Compensation function name.
    pub compensation_fn: String,
    /// Compensation function version.
    pub compensation_version: String,
    /// Compensation arguments as JSON.
    pub compensation_args: serde_json::Value,
    /// Error message from the failed compensation.
    pub error_message: String,
    /// Number of retry attempts.
    pub retry_count: i32,
}

/// Repository for dead-letter operations.
#[derive(Clone)]
pub struct DeadLetterStore {
    pool: PgPool,
}

impl DeadLetterStore {
    /// Create a new dead-letter store over the given PostgreSQL pool.
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// Create a dead-letter record from a failed compensation.
    ///
    /// Moves the effect to the dead-letter table with state `failed`.
    #[instrument(skip(self, params), fields(org_id = %params.org_id, effect_id = %params.effect_id))]
    pub async fn create(&self, params: CreateDeadLetterParams) -> UndoLogResult<DeadLetterRecord> {
        let row = sqlx::query(
            r#"
            INSERT INTO undolog_dead_letters (
                org_id, session_id, effect_id, undo_id,
                compensation_fn, compensation_version, compensation_args,
                error_message, retry_count, state
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'failed')
            RETURNING dead_letter_id, org_id, session_id, effect_id, undo_id,
                      compensation_fn, compensation_version, compensation_args,
                      error_message, retry_count, state, created_at, updated_at
            "#,
        )
        .bind(*params.org_id.as_uuid())
        .bind(*params.session_id.as_uuid())
        .bind(*params.effect_id.as_uuid())
        .bind(*params.undo_id.as_uuid())
        .bind(&params.compensation_fn)
        .bind(&params.compensation_version)
        .bind(params.compensation_args)
        .bind(&params.error_message)
        .bind(params.retry_count)
        .fetch_one(&self.pool)
        .await?;

        let record = parse_row(&row)?;
        debug!(
            dead_letter_id = %record.dead_letter_id,
            "Created dead-letter record"
        );
        Ok(record)
    }

    /// Query dead letters by state for an organization.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn list_by_state(
        &self,
        org_id: OrgId,
        state: DeadLetterState,
    ) -> UndoLogResult<Vec<DeadLetterRecord>> {
        let rows = sqlx::query(
            r#"
            SELECT dead_letter_id, org_id, session_id, effect_id, undo_id,
                   compensation_fn, compensation_version, compensation_args,
                   error_message, retry_count, state, created_at, updated_at
            FROM undolog_dead_letters
            WHERE org_id = $1 AND state = $2
            ORDER BY created_at DESC
            "#,
        )
        .bind(*org_id.as_uuid())
        .bind(state.as_str())
        .fetch_all(&self.pool)
        .await?;

        let records = rows.iter().map(parse_row).collect::<UndoLogResult<Vec<_>>>()?;
        debug!(
            org_id = %org_id,
            state = state.as_str(),
            count = records.len(),
            "Listed dead letters by state"
        );
        Ok(records)
    }

    /// Retry a dead-letter record by resetting its state to `retrying`.
    ///
    /// Returns the updated record, or an error if the record is not in `failed` state.
    #[instrument(skip(self), fields(dead_letter_id = %dead_letter_id))]
    pub async fn retry(&self, dead_letter_id: uuid::Uuid) -> UndoLogResult<DeadLetterRecord> {
        let row = sqlx::query(
            r#"
            UPDATE undolog_dead_letters
            SET state = 'retrying', retry_count = retry_count + 1
            WHERE dead_letter_id = $1 AND state = 'failed'
            RETURNING dead_letter_id, org_id, session_id, effect_id, undo_id,
                      compensation_fn, compensation_version, compensation_args,
                      error_message, retry_count, state, created_at, updated_at
            "#,
        )
        .bind(dead_letter_id)
        .fetch_one(&self.pool)
        .await?;

        let record = parse_row(&row)?;
        debug!(
            dead_letter_id = %record.dead_letter_id,
            "Retried dead-letter record"
        );
        Ok(record)
    }

    /// Skip a dead-letter record by marking it as `skipped`.
    ///
    /// Returns the updated record, or an error if the record is not in `failed` state.
    #[instrument(skip(self), fields(dead_letter_id = %dead_letter_id))]
    pub async fn skip(&self, dead_letter_id: uuid::Uuid) -> UndoLogResult<DeadLetterRecord> {
        let row = sqlx::query(
            r#"
            UPDATE undolog_dead_letters
            SET state = 'skipped'
            WHERE dead_letter_id = $1 AND state = 'failed'
            RETURNING dead_letter_id, org_id, session_id, effect_id, undo_id,
                      compensation_fn, compensation_version, compensation_args,
                      error_message, retry_count, state, created_at, updated_at
            "#,
        )
        .bind(dead_letter_id)
        .fetch_one(&self.pool)
        .await?;

        let record = parse_row(&row)?;
        debug!(
            dead_letter_id = %record.dead_letter_id,
            "Skipped dead-letter record"
        );
        Ok(record)
    }

    /// Get a single dead-letter record by ID.
    #[instrument(skip(self), fields(dead_letter_id = %dead_letter_id))]
    pub async fn get(&self, dead_letter_id: uuid::Uuid) -> UndoLogResult<Option<DeadLetterRecord>> {
        let row = sqlx::query(
            r#"
            SELECT dead_letter_id, org_id, session_id, effect_id, undo_id,
                   compensation_fn, compensation_version, compensation_args,
                   error_message, retry_count, state, created_at, updated_at
            FROM undolog_dead_letters
            WHERE dead_letter_id = $1
            "#,
        )
        .bind(dead_letter_id)
        .fetch_optional(&self.pool)
        .await?;

        row.map(|r| parse_row(&r)).transpose()
    }
}

/// Parse a database row into a DeadLetterRecord.
fn parse_row(row: &sqlx::postgres::PgRow) -> UndoLogResult<DeadLetterRecord> {
    let state_str: String = row.try_get("state")?;
    let state = DeadLetterState::parse_str(&state_str).ok_or_else(|| {
        undolog_types::errors::UndoLogError::Internal(format!("Invalid state: {state_str}"))
    })?;

    Ok(DeadLetterRecord {
        dead_letter_id: row.try_get("dead_letter_id")?,
        org_id: OrgId::from_uuid(row.try_get("org_id")?),
        session_id: SessionId::from_uuid(row.try_get("session_id")?),
        effect_id: EffectId::from_uuid(row.try_get("effect_id")?),
        undo_id: UndoId::from_uuid(row.try_get("undo_id")?),
        compensation_fn: row.try_get("compensation_fn")?,
        compensation_version: row.try_get("compensation_version")?,
        compensation_args: row.try_get("compensation_args")?,
        error_message: row.try_get("error_message")?,
        retry_count: row.try_get("retry_count")?,
        state,
        created_at: row.try_get("created_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dead_letter_state_as_str() {
        assert_eq!(DeadLetterState::Failed.as_str(), "failed");
        assert_eq!(DeadLetterState::Retrying.as_str(), "retrying");
        assert_eq!(DeadLetterState::Skipped.as_str(), "skipped");
    }

    #[test]
    fn test_dead_letter_state_parse_str() {
        assert_eq!(DeadLetterState::parse_str("failed"), Some(DeadLetterState::Failed));
        assert_eq!(DeadLetterState::parse_str("retrying"), Some(DeadLetterState::Retrying));
        assert_eq!(DeadLetterState::parse_str("skipped"), Some(DeadLetterState::Skipped));
        assert_eq!(DeadLetterState::parse_str("invalid"), None);
    }
}
