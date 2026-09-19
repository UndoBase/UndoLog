//! Automated partition management for effect log retention.
//!
//! Provides lifecycle management for the monthly-range partitioned
//! `undolog_effect_log` table: creation of future partitions, deletion
//! of expired partitions, and metrics collection.

use chrono::{NaiveDate, Utc};
use sqlx::PgPool;
use tracing::{debug, info, instrument, warn};

use undolog_types::errors::{UndoLogError, UndoLogResult};

/// Configuration for partition lifecycle management.
#[derive(Debug, Clone, PartialEq)]
pub struct PartitionConfig {
    /// Number of months to retain partitions beyond the current month.
    ///
    /// Partitions older than `current_month - retention_months` are eligible
    /// for deletion. Minimum value is 1.
    pub retention_months: u32,

    /// Number of months ahead of the current month to pre-create partitions.
    ///
    /// Ensures partitions exist for the near future so inserts never fail.
    /// Minimum value is 1.
    pub create_months_ahead: u32,
}

impl PartitionConfig {
    /// Create a new partition configuration.
    ///
    /// # Panics
    ///
    /// Panics if `retention_months` or `create_months_ahead` is less than 1.
    pub fn new(retention_months: u32, create_months_ahead: u32) -> Self {
        assert!(retention_months >= 1, "retention_months must be at least 1");
        assert!(create_months_ahead >= 1, "create_months_ahead must be at least 1");
        Self { retention_months, create_months_ahead }
    }
}

impl Default for PartitionConfig {
    /// Default: retain 12 months, create 3 months ahead.
    fn default() -> Self {
        Self::new(12, 3)
    }
}

/// Metadata about an existing partition.
#[derive(Debug, Clone, PartialEq)]
pub struct PartitionInfo {
    /// Partition table name (e.g. `undolog_effect_log_2026_01`).
    pub name: String,
    /// Start of the partition's date range (inclusive).
    pub range_start: NaiveDate,
    /// End of the partition's date range (exclusive).
    pub range_end: NaiveDate,
    /// Number of rows in the partition.
    pub row_count: i64,
}

/// Result of a partition drop operation.
#[derive(Debug, Clone, PartialEq)]
pub struct PartitionDropResult {
    /// Number of partitions dropped.
    pub dropped_count: u32,
    /// Names of dropped partitions.
    pub dropped_names: Vec<String>,
    /// Total rows deleted across all dropped partitions.
    pub total_rows_deleted: i64,
}

/// Result of a partition management cycle.
#[derive(Debug, Clone, PartialEq)]
pub struct PartitionCycleResult {
    /// Partitions created in this cycle.
    pub created: Vec<String>,
    /// Partitions dropped in this cycle.
    pub dropped: PartitionDropResult,
}

/// Repository for partition lifecycle operations.
#[derive(Clone)]
pub struct PartitionManager {
    pool: PgPool,
}

impl PartitionManager {
    /// Create a new partition manager over the given PostgreSQL pool.
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    /// List all existing partitions for `undolog_effect_log`.
    ///
    /// Returns partitions ordered by their date range start.
    /// Row counts are exact (via COUNT(*)) for reliable drop decisions.
    #[instrument(skip(self))]
    pub async fn list_partitions(&self) -> UndoLogResult<Vec<PartitionInfo>> {
        // Query pg_inherits for partition names, then COUNT(*) each for accuracy.
        let names: Vec<String> = sqlx::query_scalar(
            r#"
            SELECT child.relname
            FROM pg_inherits i
            JOIN pg_class parent ON i.inhparent = parent.oid
            JOIN pg_class child ON i.inhrelid = child.oid
            WHERE parent.relname = 'undolog_effect_log'
            ORDER BY child.relname
            "#,
        )
        .fetch_all(&self.pool)
        .await?;

        let mut partitions = Vec::new();
        for name in names {
            if let Some((start, end)) = parse_partition_range(&name) {
                let row_count: i64 =
                    sqlx::query_scalar(&format!("SELECT COUNT(*)::bigint FROM {name}"))
                        .fetch_one(&self.pool)
                        .await?;

                partitions.push(PartitionInfo {
                    name,
                    range_start: start,
                    range_end: end,
                    row_count,
                });
            }
        }

        debug!(count = partitions.len(), "Listed effect log partitions");
        Ok(partitions)
    }

    /// Create a monthly partition if it does not already exist.
    ///
    /// The partition is named `undolog_effect_log_YYYY_MM` and covers the
    /// full month. Uses `IF NOT EXISTS` for idempotency.
    ///
    /// Returns the partition name if created, or `None` if it already existed.
    #[instrument(skip(self))]
    pub async fn create_partition(&self, year: i32, month: u32) -> UndoLogResult<Option<String>> {
        let partition_name = format!("undolog_effect_log_{year}_{month:02}");

        // Calculate the month range.
        let range_start = NaiveDate::from_ymd_opt(year, month, 1)
            .ok_or_else(|| UndoLogError::Internal("Invalid date for partition".into()))?;

        let (next_year, next_month) = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
        let range_end = NaiveDate::from_ymd_opt(next_year, next_month, 1)
            .ok_or_else(|| UndoLogError::Internal("Invalid date for partition".into()))?;

        // Check if partition already exists.
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_class WHERE relname = $1)")
                .bind(&partition_name)
                .fetch_one(&self.pool)
                .await?;

        if exists {
            debug!(partition = %partition_name, "Partition already exists");
            return Ok(None);
        }

        // Create the partition with BRIN index.
        let ddl = format!(
            "CREATE TABLE IF NOT EXISTS {} PARTITION OF undolog_effect_log \
             FOR VALUES FROM ('{}') TO ('{}')",
            partition_name,
            range_start.format("%Y-%m-%d"),
            range_end.format("%Y-%m-%d"),
        );
        sqlx::query(&ddl).execute(&self.pool).await?;

        // Add BRIN index for append-only time correlation.
        let brin_index = format!(
            "CREATE INDEX IF NOT EXISTS idx_vel_{}_brin ON {} USING BRIN (executed_at) \
             WITH (pages_per_range = 32)",
            partition_name.strip_prefix("undolog_effect_log_").unwrap_or(&partition_name),
            partition_name,
        );
        sqlx::query(&brin_index).execute(&self.pool).await?;

        // Add unique index on call_signature for exactly-once enforcement.
        let sig_index = format!(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_vel_{}_sig ON {} (call_signature)",
            partition_name.strip_prefix("undolog_effect_log_").unwrap_or(&partition_name),
            partition_name,
        );
        sqlx::query(&sig_index).execute(&self.pool).await?;

        info!(partition = %partition_name, "Created partition");
        Ok(Some(partition_name))
    }

    /// Drop a partition that is older than the retention cutoff.
    ///
    /// The partition must have zero rows (use `mark_eligible` first to
    /// soft-delete rows, then hard-delete them before dropping).
    ///
    /// Returns `true` if dropped, `false` if the partition has rows or
    /// does not exist.
    #[instrument(skip(self), fields(partition = %partition_name))]
    pub async fn drop_partition(&self, partition_name: &str) -> UndoLogResult<bool> {
        // Verify the partition exists and is empty.
        let exists: bool =
            sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_class WHERE relname = $1)")
                .bind(partition_name)
                .fetch_one(&self.pool)
                .await?;

        if !exists {
            warn!(partition = %partition_name, "Partition does not exist");
            return Ok(false);
        }

        let row_count: i64 =
            sqlx::query_scalar(&format!("SELECT COUNT(*)::bigint FROM {}", partition_name,))
                .fetch_one(&self.pool)
                .await?;

        if row_count > 0 {
            warn!(
                partition = %partition_name,
                row_count,
                "Partition is not empty, refusing to drop"
            );
            return Ok(false);
        }

        // Drop the partition (detaches it from the parent).
        sqlx::query(&format!("DROP TABLE IF EXISTS {}", partition_name,))
            .execute(&self.pool)
            .await?;

        info!(partition = %partition_name, "Dropped partition");
        Ok(true)
    }

    /// Delete all rows in a partition.
    ///
    /// Used to empty a partition before dropping it. Returns the number
    /// of rows deleted. The partition remains attached to the parent
    /// table after truncation.
    #[instrument(skip(self), fields(partition = %partition_name))]
    pub async fn truncate_partition(&self, partition_name: &str) -> UndoLogResult<i64> {
        let result =
            sqlx::query(&format!("DELETE FROM {}", partition_name,)).execute(&self.pool).await?;

        let deleted = result.rows_affected() as i64;
        debug!(partition = %partition_name, deleted, "Truncated partition");
        Ok(deleted)
    }

    /// Ensure partitions exist for the current month and N months ahead.
    ///
    /// Creates any missing partitions within the window. Returns the
    /// names of partitions that were created.
    #[instrument(skip(self))]
    pub async fn ensure_future_partitions(
        &self,
        config: &PartitionConfig,
    ) -> UndoLogResult<Vec<String>> {
        let now = Utc::now();
        let current_year = now.format("%Y").to_string().parse::<i32>().unwrap();
        let current_month = now.format("%m").to_string().parse::<u32>().unwrap();

        let mut created = Vec::new();
        for offset in 0..config.create_months_ahead {
            let m = current_month + offset;
            let y = current_year + (m as i32 - 1) / 12;
            let m = ((m - 1) % 12) + 1;

            if let Some(name) = self.create_partition(y, m).await? {
                created.push(name);
            }
        }

        debug!(created = created.len(), "Ensured future partitions");
        Ok(created)
    }

    /// Drop partitions older than the retention period.
    ///
    /// Partitions before `current_month - retention_months` are truncated
    /// (rows deleted) and then dropped. Returns the drop result with counts.
    #[instrument(skip(self))]
    pub async fn drop_expired_partitions(
        &self,
        config: &PartitionConfig,
    ) -> UndoLogResult<PartitionDropResult> {
        let now = Utc::now();
        let current_year = now.format("%Y").to_string().parse::<i32>().unwrap();
        let current_month = now.format("%m").to_string().parse::<u32>().unwrap();

        // Calculate the cutoff month.
        let cutoff_month_total =
            (current_year * 12 + current_month as i32) - config.retention_months as i32;
        let cutoff_year = cutoff_month_total / 12;
        let cutoff_month = ((cutoff_month_total % 12) as u32) + 1;

        let cutoff_date = NaiveDate::from_ymd_opt(cutoff_year, cutoff_month, 1)
            .ok_or_else(|| UndoLogError::Internal("Invalid cutoff date".into()))?;

        let partitions = self.list_partitions().await?;
        let mut dropped_names = Vec::new();
        let mut total_rows_deleted: i64 = 0;

        for partition in &partitions {
            if partition.range_start < cutoff_date {
                // Truncate the partition first (delete all rows).
                if partition.row_count > 0 {
                    let deleted = self.truncate_partition(&partition.name).await?;
                    total_rows_deleted += deleted;
                }

                // Then drop the empty partition.
                if self.drop_partition(&partition.name).await? {
                    dropped_names.push(partition.name.clone());
                }
            }
        }

        let dropped_count = dropped_names.len() as u32;
        debug!(dropped_count, total_rows_deleted, "Dropped expired partitions");

        Ok(PartitionDropResult { dropped_count, dropped_names, total_rows_deleted })
    }

    /// Run a full partition management cycle.
    ///
    /// 1. Ensure future partitions exist.
    /// 2. Drop expired partitions.
    ///
    /// Returns the combined result.
    #[instrument(skip(self))]
    pub async fn run_cycle(&self, config: &PartitionConfig) -> UndoLogResult<PartitionCycleResult> {
        let created = self.ensure_future_partitions(config).await?;
        let dropped = self.drop_expired_partitions(config).await?;

        info!(
            created = created.len(),
            dropped = dropped.dropped_count,
            "Partition management cycle complete"
        );

        Ok(PartitionCycleResult { created, dropped })
    }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/// Parse a partition name like `undolog_effect_log_2026_01` into (start, end) dates.
fn parse_partition_range(name: &str) -> Option<(NaiveDate, NaiveDate)> {
    let suffix = name.strip_prefix("undolog_effect_log_")?;
    let parts: Vec<&str> = suffix.split('_').collect();
    if parts.len() != 2 {
        return None;
    }

    let year: i32 = parts[0].parse().ok()?;
    let month: u32 = parts[1].parse().ok()?;

    // Validate month is in range 1-12.
    if !(1..=12).contains(&month) {
        return None;
    }

    let start = NaiveDate::from_ymd_opt(year, month, 1)?;
    let (next_year, next_month) = if month == 12 { (year + 1, 1) } else { (year, month + 1) };
    let end = NaiveDate::from_ymd_opt(next_year, next_month, 1)?;

    Some((start, end))
}

// ── Unit tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_partition_config_new() {
        let config = PartitionConfig::new(6, 3);
        assert_eq!(config.retention_months, 6);
        assert_eq!(config.create_months_ahead, 3);
    }

    #[test]
    fn test_partition_config_default() {
        let config = PartitionConfig::default();
        assert_eq!(config.retention_months, 12);
        assert_eq!(config.create_months_ahead, 3);
    }

    #[test]
    #[should_panic(expected = "retention_months must be at least 1")]
    fn test_partition_config_zero_retention_panics() {
        PartitionConfig::new(0, 3);
    }

    #[test]
    #[should_panic(expected = "create_months_ahead must be at least 1")]
    fn test_partition_config_zero_ahead_panics() {
        PartitionConfig::new(12, 0);
    }

    #[test]
    fn test_parse_partition_range_valid() {
        let (start, end) = parse_partition_range("undolog_effect_log_2026_01").unwrap();
        assert_eq!(start, NaiveDate::from_ymd_opt(2026, 1, 1).unwrap());
        assert_eq!(end, NaiveDate::from_ymd_opt(2026, 2, 1).unwrap());
    }

    #[test]
    fn test_parse_partition_range_december() {
        let (start, end) = parse_partition_range("undolog_effect_log_2025_12").unwrap();
        assert_eq!(start, NaiveDate::from_ymd_opt(2025, 12, 1).unwrap());
        assert_eq!(end, NaiveDate::from_ymd_opt(2026, 1, 1).unwrap());
    }

    #[test]
    fn test_parse_partition_range_invalid_prefix() {
        assert!(parse_partition_range("other_table_2026_01").is_none());
    }

    #[test]
    fn test_parse_partition_range_invalid_format() {
        assert!(parse_partition_range("undolog_effect_log_2026").is_none());
        assert!(parse_partition_range("undolog_effect_log_2026_01_extra").is_none());
    }

    #[test]
    fn test_parse_partition_range_non_numeric() {
        assert!(parse_partition_range("undolog_effect_log_20XX_01").is_none());
    }

    #[test]
    fn test_parse_partition_range_invalid_month() {
        // Month 0
        assert!(parse_partition_range("undolog_effect_log_2026_00").is_none());
        // Month 13
        assert!(parse_partition_range("undolog_effect_log_2026_13").is_none());
        // Month 99
        assert!(parse_partition_range("undolog_effect_log_2026_99").is_none());
    }

    #[test]
    fn test_partition_cycle_result_equality() {
        let result1 = PartitionCycleResult {
            created: vec!["partition_1".to_string()],
            dropped: PartitionDropResult {
                dropped_count: 2,
                dropped_names: vec!["old_1".to_string(), "old_2".to_string()],
                total_rows_deleted: 100,
            },
        };
        let result2 = PartitionCycleResult {
            created: vec!["partition_1".to_string()],
            dropped: PartitionDropResult {
                dropped_count: 2,
                dropped_names: vec!["old_1".to_string(), "old_2".to_string()],
                total_rows_deleted: 100,
            },
        };
        assert_eq!(result1, result2);
    }

    #[test]
    fn test_partition_drop_result_empty() {
        let result =
            PartitionDropResult { dropped_count: 0, dropped_names: vec![], total_rows_deleted: 0 };
        assert_eq!(result.dropped_count, 0);
        assert!(result.dropped_names.is_empty());
        assert_eq!(result.total_rows_deleted, 0);
    }

    #[test]
    fn test_partition_info_fields() {
        let info = PartitionInfo {
            name: "undolog_effect_log_2026_01".to_string(),
            range_start: NaiveDate::from_ymd_opt(2026, 1, 1).unwrap(),
            range_end: NaiveDate::from_ymd_opt(2026, 2, 1).unwrap(),
            row_count: 1234,
        };
        assert_eq!(info.name, "undolog_effect_log_2026_01");
        assert_eq!(info.row_count, 1234);
    }
}
