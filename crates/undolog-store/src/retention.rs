//! Effect retention policy implementation.
//!
//! Provides per-org configurable TTL-based retention for the effect log.
//! Effects older than the configured TTL are marked for deletion.

use chrono::Utc;
use sqlx::PgPool;
use tracing::{debug, instrument};

use undolog_types::{config::RetentionConfig, errors::UndoLogResult, ids::OrgId};

/// Result of a retention sweep operation.
#[derive(Debug, Clone, PartialEq)]
pub struct RetentionSweepResult {
    /// Number of effects marked for deletion.
    pub marked_for_deletion: i64,
    /// Number of effects actually deleted (soft-delete or hard-delete).
    pub deleted: i64,
}

/// Repository for effect retention operations.
#[derive(Clone)]
pub struct RetentionStore {
    pool: PgPool,
}

impl RetentionStore {
    /// Create a new retention store over the given PostgreSQL pool.
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// Count effects eligible for deletion for a given organization.
    ///
    /// Returns the number of effects with `executed_at` older than the
    /// retention cutoff timestamp.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn count_eligible(
        &self,
        org_id: OrgId,
        config: &RetentionConfig,
    ) -> UndoLogResult<i64> {
        let cutoff = config.cutoff(Utc::now());
        let count: (i64,) = sqlx::query_as(
            r#"
            SELECT COUNT(*)::bigint
            FROM undolog_effect_log
            WHERE org_id = $1
              AND executed_at < $2
            "#,
        )
        .bind(*org_id.as_uuid())
        .bind(cutoff)
        .fetch_one(&self.pool)
        .await?;

        debug!(org_id = %org_id, cutoff = %cutoff, count = count.0, "Counted eligible effects");
        Ok(count.0)
    }

    /// Mark effects older than the retention cutoff as deleted.
    ///
    /// This is a soft-delete: sets `state = 'deleted'` for effects where
    /// `executed_at < cutoff` and `state` is not already terminal.
    ///
    /// Returns the number of effects marked.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn mark_eligible(
        &self,
        org_id: OrgId,
        config: &RetentionConfig,
    ) -> UndoLogResult<i64> {
        let cutoff = config.cutoff(Utc::now());

        let result = sqlx::query(
            r#"
            UPDATE undolog_effect_log
            SET state = 'deleted'::undolog_effect_state
            WHERE org_id = $1
              AND executed_at < $2
              AND state NOT IN (
                'compensated'::undolog_effect_state,
                'failed'::undolog_effect_state,
                'deleted'::undolog_effect_state
              )
            "#,
        )
        .bind(*org_id.as_uuid())
        .bind(cutoff)
        .execute(&self.pool)
        .await?;

        let marked = result.rows_affected() as i64;
        debug!(org_id = %org_id, cutoff = %cutoff, marked, "Marked effects for deletion");
        Ok(marked)
    }

    /// Execute a full retention sweep for an organization.
    ///
    /// First counts eligible effects, then marks them for deletion.
    /// Returns the sweep result with counts.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn sweep(
        &self,
        org_id: OrgId,
        config: &RetentionConfig,
    ) -> UndoLogResult<RetentionSweepResult> {
        let marked = self.mark_eligible(org_id, config).await?;

        Ok(RetentionSweepResult { marked_for_deletion: marked, deleted: marked })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_retention_sweep_result_equality() {
        let result1 = RetentionSweepResult { marked_for_deletion: 10, deleted: 10 };
        let result2 = RetentionSweepResult { marked_for_deletion: 10, deleted: 10 };
        assert_eq!(result1, result2);
    }

    #[test]
    fn test_retention_sweep_result_different_counts() {
        let result1 = RetentionSweepResult { marked_for_deletion: 10, deleted: 10 };
        let result2 = RetentionSweepResult { marked_for_deletion: 5, deleted: 5 };
        assert_ne!(result1, result2);
    }
}
