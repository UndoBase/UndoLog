//! Integration tests for the approval timeout processor.
//!
//! These tests require `TEST_DATABASE_URL` environment variable to be set.
//! They test the full timeout processing logic with a real PostgreSQL database.

#![cfg(all(test, feature = "pg"))]

use undolog_engine::EngineConfig;
use undolog_store::ApprovalStore;
use undolog_types::{
    approval::{ApprovalRequest, ApprovalState},
    ids::{EffectId, OrgId, SessionId},
};

// ── Test helpers ───────────────────────────────────────────────────────────

/// Return database URL from environment, or skip the test entirely.
fn require_database_url() -> String {
    match std::env::var("TEST_DATABASE_URL") {
        Ok(url) => url,
        Err(_) => {
            eprintln!("TEST_DATABASE_URL not set, skipping integration test");
            std::process::exit(0);
        }
    }
}

/// Shared pool for all tests in this module (created once, reused).
static TEST_POOL: std::sync::OnceLock<sqlx::PgPool> = std::sync::OnceLock::new();

fn pool() -> &'static sqlx::PgPool {
    TEST_POOL.get_or_init(|| {
        let url = require_database_url();
        sqlx::PgPool::connect_lazy(&url).expect("Failed to create lazy pool for tests")
    })
}

/// Helper: create a fresh ApprovalStore using the shared pool.
fn setup_approval_store() -> ApprovalStore {
    ApprovalStore::new(pool().clone())
}

/// Helper: create a test org in the database.
async fn create_test_org(org_id: &OrgId) {
    sqlx::query(
        r#"
        INSERT INTO undolog_orgs (org_id, slug, name, created_at)
        VALUES ($1, 'test-org', 'Test Organisation', now())
        ON CONFLICT (org_id) DO NOTHING
        "#,
    )
    .bind(*org_id.as_uuid())
    .execute(pool())
    .await
    .expect("Failed to create test org");
}

/// Helper: create a test session in the database.
async fn create_test_session(org_id: &OrgId, session_id: &SessionId) {
    sqlx::query(
        r#"
        INSERT INTO undolog_sessions (session_id, org_id, workflow_id, state, created_at)
        VALUES ($1, $2, 'test-workflow', 'active', now())
        ON CONFLICT (session_id) DO NOTHING
        "#,
    )
    .bind(*session_id.as_uuid())
    .bind(*org_id.as_uuid())
    .execute(pool())
    .await
    .expect("Failed to create test session");
}

/// Helper: create a pending approval request that has already timed out.
async fn create_timed_out_approval(
    store: &ApprovalStore,
    org_id: &OrgId,
    session_id: &SessionId,
    auto_approve: bool,
) -> undolog_types::ids::ApprovalRequestId {
    let req = ApprovalRequest {
        approval_request_id: undolog_types::ids::ApprovalRequestId::from(uuid::Uuid::new_v4()),
        org_id: *org_id,
        session_id: *session_id,
        effect_id: EffectId::from(uuid::Uuid::new_v4()),
        tool_name: "test_tool".to_string(),
        irreversibility_reason: "Test irreversibility".to_string(),
        risk_tags: vec!["test".to_string()],
        estimated_impact: Some("Test impact".to_string()),
        proposed_args: serde_json::json!({"key": "value"}),
        agent_context: serde_json::json!({}),
        state: ApprovalState::Pending,
        timeout_at: chrono::Utc::now() - chrono::Duration::seconds(3600),
        auto_approve_on_timeout: auto_approve,
        resolved_at: None,
        resolved_by: None,
        approved_args: None,
        created_at: chrono::Utc::now(),
    };
    store.create(&req).await.expect("Failed to create approval request");
    req.approval_request_id
}

/// Helper: create a pending approval request that has NOT timed out yet.
async fn create_pending_approval(
    store: &ApprovalStore,
    org_id: &OrgId,
    session_id: &SessionId,
) -> undolog_types::ids::ApprovalRequestId {
    let req = ApprovalRequest {
        approval_request_id: undolog_types::ids::ApprovalRequestId::from(uuid::Uuid::new_v4()),
        org_id: *org_id,
        session_id: *session_id,
        effect_id: EffectId::from(uuid::Uuid::new_v4()),
        tool_name: "test_tool".to_string(),
        irreversibility_reason: "Test irreversibility".to_string(),
        risk_tags: vec!["test".to_string()],
        estimated_impact: Some("Test impact".to_string()),
        proposed_args: serde_json::json!({"key": "value"}),
        agent_context: serde_json::json!({}),
        state: ApprovalState::Pending,
        timeout_at: chrono::Utc::now() + chrono::Duration::hours(24),
        auto_approve_on_timeout: false,
        resolved_at: None,
        resolved_by: None,
        approved_args: None,
        created_at: chrono::Utc::now(),
    };
    store.create(&req).await.expect("Failed to create approval request");
    req.approval_request_id
}

// ── EngineConfig bridge test ───────────────────────────────────────────────

#[test]
fn test_engine_config_approval_timeout_config() {
    let config = EngineConfig {
        lock_max_attempts: 3,
        lock_retry_ms: 100,
        approval_timeout_secs: 7200,
        auto_approve_on_timeout: true,
        timeout_check_interval_secs: 30,
    };

    let timeout_config = config.approval_timeout_config();
    assert_eq!(timeout_config.timeout_secs, 7200);
    assert!(timeout_config.auto_approve);
    assert_eq!(timeout_config.check_interval_secs, 30);
}

// ── Integration tests: timeout processing ──────────────────────────────────

#[tokio::test]
async fn test_approval_expires_after_configurable_duration() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let approval_id = create_timed_out_approval(&store, &org_id, &session_id, false).await;

    let count = store.process_timeouts(&org_id).await.expect("Failed to process timeouts");
    assert_eq!(count, 1);

    let req = store.get(&org_id, &approval_id).await.expect("Failed to get approval").unwrap();
    assert_eq!(req.state, ApprovalState::TimedOut);
    assert!(req.resolved_at.is_some());
    assert_eq!(req.resolved_by.as_deref(), Some("system:timeout"));
}

#[tokio::test]
async fn test_auto_approve_policy_transitions_to_auto_approved() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let approval_id = create_timed_out_approval(&store, &org_id, &session_id, true).await;

    let count = store.process_timeouts(&org_id).await.expect("Failed to process timeouts");
    assert_eq!(count, 1);

    let req = store.get(&org_id, &approval_id).await.expect("Failed to get approval").unwrap();
    assert_eq!(req.state, ApprovalState::AutoApproved);
    assert!(req.resolved_at.is_some());
    assert_eq!(req.resolved_by.as_deref(), Some("system:timeout"));
}

#[tokio::test]
async fn test_no_auto_approve_transitions_to_timed_out() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let approval_id = create_timed_out_approval(&store, &org_id, &session_id, false).await;

    let count = store.process_timeouts(&org_id).await.expect("Failed to process timeouts");
    assert_eq!(count, 1);

    let req = store.get(&org_id, &approval_id).await.expect("Failed to get approval").unwrap();
    assert_eq!(req.state, ApprovalState::TimedOut);
}

#[tokio::test]
async fn test_approval_events_recorded() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let approval_id1 = create_timed_out_approval(&store, &org_id, &session_id, false).await;
    let approval_id2 = create_timed_out_approval(&store, &org_id, &session_id, true).await;

    let count = store.process_timeouts(&org_id).await.expect("Failed to process timeouts");
    assert_eq!(count, 2);

    let events1: Vec<(String, String)> = sqlx::query_as(
        r#"
        SELECT action::text, actor
        FROM undolog_approval_events
        WHERE approval_request_id = $1
        ORDER BY occurred_at ASC
        "#,
    )
    .bind(*approval_id1.as_uuid())
    .fetch_all(pool())
    .await
    .expect("Failed to fetch events");
    assert_eq!(events1.len(), 1);
    assert_eq!(events1[0].0, "timeout");
    assert_eq!(events1[0].1, "system:timeout");

    let events2: Vec<(String, String)> = sqlx::query_as(
        r#"
        SELECT action::text, actor
        FROM undolog_approval_events
        WHERE approval_request_id = $1
        ORDER BY occurred_at ASC
        "#,
    )
    .bind(*approval_id2.as_uuid())
    .fetch_all(pool())
    .await
    .expect("Failed to fetch events");
    assert_eq!(events2.len(), 1);
    assert_eq!(events2[0].0, "timeout");
    assert_eq!(events2[0].1, "system:timeout");
}

#[tokio::test]
async fn test_pending_approvals_not_affected() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let timed_out_id = create_timed_out_approval(&store, &org_id, &session_id, false).await;
    let pending_id = create_pending_approval(&store, &org_id, &session_id).await;

    let count = store.process_timeouts(&org_id).await.expect("Failed to process timeouts");
    assert_eq!(count, 1);

    let timed_out_req = store.get(&org_id, &timed_out_id).await.unwrap().unwrap();
    assert_eq!(timed_out_req.state, ApprovalState::TimedOut);

    let pending_req = store.get(&org_id, &pending_id).await.unwrap().unwrap();
    assert_eq!(pending_req.state, ApprovalState::Pending);
    assert!(pending_req.resolved_at.is_none());
}

#[tokio::test]
async fn test_list_orgs_with_pending_approvals() {
    let store = setup_approval_store();
    let org_id1 = OrgId::from(uuid::Uuid::new_v4());
    let org_id2 = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id1).await;
    create_test_org(&org_id2).await;
    create_test_session(&org_id1, &session_id).await;

    create_pending_approval(&store, &org_id1, &session_id).await;

    let orgs = store.list_orgs_with_pending_approvals().await.unwrap();
    assert!(orgs.contains(&org_id1));
    assert!(!orgs.contains(&org_id2));
}

#[tokio::test]
async fn test_mixed_auto_approve_settings() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let id_auto = create_timed_out_approval(&store, &org_id, &session_id, true).await;
    let id_no_auto = create_timed_out_approval(&store, &org_id, &session_id, false).await;

    let count = store.process_timeouts(&org_id).await.expect("Failed to process timeouts");
    assert_eq!(count, 2);

    let req_auto = store.get(&org_id, &id_auto).await.unwrap().unwrap();
    assert_eq!(req_auto.state, ApprovalState::AutoApproved);

    let req_no_auto = store.get(&org_id, &id_no_auto).await.unwrap().unwrap();
    assert_eq!(req_no_auto.state, ApprovalState::TimedOut);
}

#[tokio::test]
async fn test_process_timeouts_idempotent() {
    let store = setup_approval_store();
    let org_id = OrgId::from(uuid::Uuid::new_v4());
    let session_id = SessionId::from(uuid::Uuid::new_v4());

    create_test_org(&org_id).await;
    create_test_session(&org_id, &session_id).await;

    let approval_id = create_timed_out_approval(&store, &org_id, &session_id, false).await;

    let count1 = store.process_timeouts(&org_id).await.unwrap();
    assert_eq!(count1, 1);

    let count2 = store.process_timeouts(&org_id).await.unwrap();
    assert_eq!(count2, 0);

    let event_count: (i64,) = sqlx::query_as(
        r#"
        SELECT COUNT(*)::bigint
        FROM undolog_approval_events
        WHERE approval_request_id = $1
        "#,
    )
    .bind(*approval_id.as_uuid())
    .fetch_one(pool())
    .await
    .unwrap();
    assert_eq!(event_count.0, 1);
}
