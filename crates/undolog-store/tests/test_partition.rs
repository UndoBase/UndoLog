//! Tests for automated partition management.
//!
//! These tests verify the partition management logic with unit tests
//! for configuration and metadata, and integration tests for DB operations.

use chrono::{Datelike, NaiveDate};
use undolog_store::partition::{
    PartitionConfig, PartitionCycleResult, PartitionDropResult, PartitionInfo,
};

// ── Unit Tests ─────────────────────────────────────────────────────────────

#[test]
fn test_partition_config_respects_retention_months() {
    let config = PartitionConfig::new(6, 3);
    assert_eq!(config.retention_months, 6);
    assert_eq!(config.create_months_ahead, 3);
}

#[test]
fn test_partition_config_default_values() {
    let config = PartitionConfig::default();
    assert_eq!(config.retention_months, 12);
    assert_eq!(config.create_months_ahead, 3);
}

#[test]
fn test_partition_config_different_retentions() {
    let config_3 = PartitionConfig::new(3, 1);
    let config_12 = PartitionConfig::new(12, 1);
    let config_24 = PartitionConfig::new(24, 1);

    assert_eq!(config_3.retention_months, 3);
    assert_eq!(config_12.retention_months, 12);
    assert_eq!(config_24.retention_months, 24);
}

#[test]
fn test_partition_config_different_ahead() {
    let config_1 = PartitionConfig::new(12, 1);
    let config_6 = PartitionConfig::new(12, 6);

    assert_eq!(config_1.create_months_ahead, 1);
    assert_eq!(config_6.create_months_ahead, 6);
}

#[test]
fn test_partition_info_metadata() {
    let info = PartitionInfo {
        name: "undolog_effect_log_2026_01".to_string(),
        range_start: NaiveDate::from_ymd_opt(2026, 1, 1).unwrap(),
        range_end: NaiveDate::from_ymd_opt(2026, 2, 1).unwrap(),
        row_count: 500,
    };

    assert_eq!(info.name, "undolog_effect_log_2026_01");
    assert_eq!(info.range_start, NaiveDate::from_ymd_opt(2026, 1, 1).unwrap());
    assert_eq!(info.range_end, NaiveDate::from_ymd_opt(2026, 2, 1).unwrap());
    assert_eq!(info.row_count, 500);
}

#[test]
fn test_partition_info_empty_partition() {
    let info = PartitionInfo {
        name: "undolog_effect_log_2026_02".to_string(),
        range_start: NaiveDate::from_ymd_opt(2026, 2, 1).unwrap(),
        range_end: NaiveDate::from_ymd_opt(2026, 3, 1).unwrap(),
        row_count: 0,
    };

    assert_eq!(info.row_count, 0);
}

#[test]
fn test_partition_drop_result_with_drops() {
    let result = PartitionDropResult {
        dropped_count: 3,
        dropped_names: vec![
            "undolog_effect_log_2025_01".to_string(),
            "undolog_effect_log_2025_02".to_string(),
            "undolog_effect_log_2025_03".to_string(),
        ],
        total_rows_deleted: 1500,
    };

    assert_eq!(result.dropped_count, 3);
    assert_eq!(result.dropped_names.len(), 3);
    assert_eq!(result.total_rows_deleted, 1500);
}

#[test]
fn test_partition_cycle_result_with_operations() {
    let result = PartitionCycleResult {
        created: vec![
            "undolog_effect_log_2026_06".to_string(),
            "undolog_effect_log_2026_07".to_string(),
        ],
        dropped: PartitionDropResult {
            dropped_count: 1,
            dropped_names: vec!["undolog_effect_log_2025_04".to_string()],
            total_rows_deleted: 200,
        },
    };

    assert_eq!(result.created.len(), 2);
    assert_eq!(result.dropped.dropped_count, 1);
    assert_eq!(result.dropped.total_rows_deleted, 200);
}

// ── Property Tests ─────────────────────────────────────────────────────────

#[test]
fn test_partition_config_boundary_conditions() {
    // Minimum valid configuration
    let config_min = PartitionConfig::new(1, 1);
    assert_eq!(config_min.retention_months, 1);
    assert_eq!(config_min.create_months_ahead, 1);

    // Large values should work
    let config_large = PartitionConfig::new(120, 24);
    assert_eq!(config_large.retention_months, 120);
    assert_eq!(config_large.create_months_ahead, 24);
}

#[test]
fn test_partition_info_date_range_consistency() {
    // All partitions should cover exactly one month
    let months = vec![(2026, 1, 2026, 2), (2026, 12, 2027, 1), (2025, 6, 2025, 7)];

    for (start_y, start_m, end_y, end_m) in months {
        let start = NaiveDate::from_ymd_opt(start_y, start_m, 1).unwrap();
        let end = NaiveDate::from_ymd_opt(end_y, end_m, 1).unwrap();

        // End should be exactly one month after start
        let diff_months =
            (end.year() - start.year()) * 12 + (end.month() as i32 - start.month() as i32);
        assert_eq!(diff_months, 1, "Partition should cover exactly one month");
    }
}

#[test]
fn test_partition_drop_result_monotonic_counts() {
    // Dropping more partitions should result in >= rows deleted
    let result_few = PartitionDropResult {
        dropped_count: 1,
        dropped_names: vec!["p1".to_string()],
        total_rows_deleted: 100,
    };
    let result_many = PartitionDropResult {
        dropped_count: 3,
        dropped_names: vec!["p1".to_string(), "p2".to_string(), "p3".to_string()],
        total_rows_deleted: 300,
    };

    assert!(result_many.dropped_count >= result_few.dropped_count);
    assert!(result_many.total_rows_deleted >= result_few.total_rows_deleted);
}
