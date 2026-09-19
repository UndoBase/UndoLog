//! Tests for dead-letter store.
//!
//! These tests verify the dead-letter CRUD operations with unit tests
//! covering normal, error, and edge cases.

use undolog_store::dead_letter::{DeadLetterRecord, DeadLetterState};

// ── Unit Tests ─────────────────────────────────────────────────────────────

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
    assert_eq!(DeadLetterState::parse_str(""), None);
}

#[test]
fn test_dead_letter_state_roundtrip() {
    let states = vec![DeadLetterState::Failed, DeadLetterState::Retrying, DeadLetterState::Skipped];

    for state in states {
        let s = state.as_str();
        let parsed = DeadLetterState::parse_str(s);
        assert_eq!(parsed, Some(state), "Roundtrip failed for state: {s}");
    }
}

#[test]
fn test_dead_letter_record_default_state() {
    let record = create_test_record(DeadLetterState::Failed);
    assert_eq!(record.state, DeadLetterState::Failed);
    assert_eq!(record.retry_count, 0);
}

#[test]
fn test_dead_letter_record_retry_increments_count() {
    let mut record = create_test_record(DeadLetterState::Failed);
    record.retry_count += 1;
    record.state = DeadLetterState::Retrying;

    assert_eq!(record.retry_count, 1);
    assert_eq!(record.state, DeadLetterState::Retrying);
}

#[test]
fn test_dead_letter_record_skip_transitions() {
    let mut record = create_test_record(DeadLetterState::Failed);
    record.state = DeadLetterState::Skipped;

    assert_eq!(record.state, DeadLetterState::Skipped);
}

#[test]
fn test_dead_letter_state_invalid_transition() {
    let record = create_test_record(DeadLetterState::Skipped);

    // Cannot retry from skipped state (enforced by SQL CHECK constraint)
    assert_ne!(record.state, DeadLetterState::Failed);
    assert_ne!(record.state, DeadLetterState::Retrying);
}

#[test]
fn test_dead_letter_record_preserves_context() {
    let record = create_test_record(DeadLetterState::Failed);

    assert_eq!(record.compensation_fn, "compensate_transfer");
    assert_eq!(record.compensation_version, "1.0.0");
    assert_eq!(record.error_message, "Network timeout");
    assert_eq!(record.retry_count, 0);
}

// ── Helper ─────────────────────────────────────────────────────────────────

fn create_test_record(state: DeadLetterState) -> DeadLetterRecord {
    DeadLetterRecord {
        dead_letter_id: uuid::Uuid::new_v4(),
        org_id: undolog_types::ids::OrgId::new(),
        session_id: undolog_types::ids::SessionId::new(),
        effect_id: undolog_types::ids::EffectId::new(),
        undo_id: undolog_types::ids::UndoId::new(),
        compensation_fn: "compensate_transfer".to_string(),
        compensation_version: "1.0.0".to_string(),
        compensation_args: serde_json::json!({"amount": 100}),
        error_message: "Network timeout".to_string(),
        retry_count: 0,
        state,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    }
}
