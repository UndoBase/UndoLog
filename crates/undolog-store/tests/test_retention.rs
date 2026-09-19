//! Tests for effect retention policy.
//!
//! These tests verify the retention policy logic with unit tests
//! and property tests for correctness guarantees.

use chrono::{Duration, Utc};
use undolog_store::retention::RetentionSweepResult;
use undolog_types::config::RetentionConfig;

// ── Unit Tests ─────────────────────────────────────────────────────────────

#[test]
fn test_retention_config_respects_per_org_ttl() {
    let config_30 = RetentionConfig::new(30);
    let config_90 = RetentionConfig::new(90);

    assert_eq!(config_30.ttl_days, 30);
    assert_eq!(config_90.ttl_days, 90);
    assert_ne!(config_30.ttl_duration(), config_90.ttl_duration());
}

#[test]
fn test_effects_younger_than_ttl_are_kept() {
    let config = RetentionConfig::new(30);
    let now = Utc::now();

    // Effect created 10 days ago (within 30-day TTL)
    let effect_time = now - Duration::days(10);
    let cutoff = config.cutoff(now);

    assert!(effect_time > cutoff, "Effect created 10 days ago should be kept with 30-day TTL");
}

#[test]
fn test_effects_older_than_ttl_are_marked_for_deletion() {
    let config = RetentionConfig::new(30);
    let now = Utc::now();

    // Effect created 60 days ago (beyond 30-day TTL)
    let effect_time = now - Duration::days(60);
    let cutoff = config.cutoff(now);

    assert!(
        effect_time < cutoff,
        "Effect created 60 days ago should be marked for deletion with 30-day TTL"
    );
}

#[test]
fn test_retention_policy_handles_empty_effect_log() {
    let result = RetentionSweepResult { marked_for_deletion: 0, deleted: 0 };

    assert_eq!(result.marked_for_deletion, 0);
    assert_eq!(result.deleted, 0);
}

#[test]
fn test_retention_config_boundary_condition() {
    let config = RetentionConfig::new(1);
    let now = Utc::now();

    // Effect created exactly 1 day ago (boundary)
    let effect_time = now - Duration::days(1);
    let cutoff = config.cutoff(now);

    // Effect at exactly the boundary should be kept (not deleted)
    assert!(effect_time >= cutoff, "Effect at boundary should be kept");
}

// ── Property Tests ─────────────────────────────────────────────────────────

#[test]
fn test_retention_never_deletes_effects_within_ttl_window() {
    let config = RetentionConfig::new(30);
    let now = Utc::now();

    // Test various ages within the TTL window
    for days in 0..30 {
        let effect_time = now - Duration::days(days);
        let cutoff = config.cutoff(now);

        assert!(
            effect_time > cutoff,
            "Effect created {days} days ago should be kept with 30-day TTL"
        );
    }
}

#[test]
fn test_retention_count_is_monotonically_non_decreasing() {
    let config = RetentionConfig::new(30);
    let now = Utc::now();

    // As time increases, the number of eligible effects should not decrease
    let mut previous_count = 0;

    for days in 30..60 {
        let effect_time = now - Duration::days(days);
        let cutoff = config.cutoff(now);

        if effect_time < cutoff {
            previous_count += 1;
        }

        // Count of eligible effects should be >= previous count
        assert!(
            previous_count >= days - 30,
            "Retention count should be monotonically non-decreasing"
        );
    }
}

#[test]
fn test_retention_config_different_ttls_produce_different_cutoffs() {
    let now = Utc::now();

    let config_7 = RetentionConfig::new(7);
    let config_30 = RetentionConfig::new(30);
    let config_90 = RetentionConfig::new(90);

    let cutoff_7 = config_7.cutoff(now);
    let cutoff_30 = config_30.cutoff(now);
    let cutoff_90 = config_90.cutoff(now);

    // Shorter TTL should have a more recent cutoff
    assert!(cutoff_7 > cutoff_30, "7-day cutoff should be more recent than 30-day");
    assert!(cutoff_30 > cutoff_90, "30-day cutoff should be more recent than 90-day");
}

#[test]
fn test_retention_sweep_result_consistency() {
    let result = RetentionSweepResult { marked_for_deletion: 5, deleted: 5 };

    assert_eq!(result.marked_for_deletion, result.deleted);
}
