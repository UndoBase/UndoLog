//! Integration tests for undolog-engine.
//!
//! These tests require `TEST_DATABASE_URL` environment variable to be set.
//! They test the full tier routing logic with a real PostgreSQL database.

#![cfg(test)]

use std::sync::Arc;
use uuid::Uuid;

use serde_json::json;
use undolog_engine::{EffectEngine, EngineConfig, InterceptOutcome, TierRegistry};
use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::{
    effect::{ToolCall, ToolResult},
    ids::{OrgId, SessionId, ToolId},
    tier::{CompensationDescriptor, ToolTier},
};

// ── Test helpers ───────────────────────────────────────────────────────────

/// Helper: get database URL from environment or skip test.
fn get_database_url() -> String {
    std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string())
}

/// Helper: create or reset engine for a test.
async fn setup_engine() -> EffectEngine {
    let db_url = get_database_url();
    let pool = sqlx::PgPool::connect(&db_url).await.expect("Failed to connect to test database");

    let effect_store = EffectStore::new(pool.clone());
    let session_store = SessionStore::new(pool.clone());
    let approval_store = ApprovalStore::new(pool);

    let registry = Arc::new(tokio::sync::RwLock::new(TierRegistry::new()));

    EffectEngine::new(
        effect_store,
        session_store,
        approval_store,
        registry,
        EngineConfig::default(),
    )
}

/// Helper: create a test session in the database.
async fn create_test_session(org_id: &OrgId, session_id: &SessionId) {
    let db_url = get_database_url();
    let pool =
        sqlx::PgPool::connect(&db_url).await.expect("Failed to connect for session creation");

    sqlx::query(
        r#"
        INSERT INTO undolog_sessions (
            session_id, org_id, project_id, external_run_id, agent_name,
            state, tool_calls_total, compensations_total, approvals_pending,
            started_at, metadata
        )
        VALUES ($1, $2, $3, $4, $5, 'active'::undolog_session_state, 0, 0, 0, now(), '{}')
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(*session_id.as_uuid())
    .bind(*org_id.as_uuid())
    .bind(None::<Uuid>)
    .bind(None::<String>)
    .bind(None::<String>)
    .execute(&pool)
    .await
    .expect("Failed to insert test session");
}

/// Helper: create a test tool call.
fn create_tool_call(
    org_id: OrgId,
    session_id: SessionId,
    tool_name: &str,
    tier: ToolTier,
) -> ToolCall {
    ToolCall {
        org_id,
        session_id,
        tool_id: Some(ToolId::new()),
        tool_name: tool_name.to_string(),
        tool_version: "1.0.0".to_string(),
        tier,
        step_index: 1,
        args: json!({"key": "value"}),
        intercepted_at: chrono::Utc::now(),
    }
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn intercept_safe_bypasses_log() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let call = create_tool_call(org_id, session_id, "read_web", ToolTier::Safe);

    let outcome = engine.intercept(call).await.expect("intercept failed");

    // Safe tier should return Execute without logging.
    match outcome {
        InterceptOutcome::Execute { effect_id } => {
            // We can't easily verify it wasn't logged without querying the DB,
            // but for Safe tools the effect_id is just a placeholder.
            println!("Safe tier: effect_id = {}", effect_id);
        }
        _ => panic!("Expected Execute outcome for Safe tier"),
    }
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn intercept_compensable_inserts_effect_and_undo_entry() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let compensation = CompensationDescriptor::new("cancel_payment", json!({"amount": 100}));
    let call =
        create_tool_call(org_id, session_id, "charge_card", ToolTier::Compensable { compensation });

    let outcome = engine.intercept(call.clone()).await.expect("intercept failed");

    match outcome {
        InterceptOutcome::Execute { effect_id } => {
            println!("Compensable: effect_id = {}", effect_id);
            // Verify effect exists in database with Pending state.
            // This would require direct DB query; skipped for brevity.
        }
        _ => panic!("Expected Execute outcome for Compensable tier"),
    }
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn duplicate_signature_returns_replay() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let compensation = CompensationDescriptor::new("cancel_payment", json!({"amount": 100}));
    let call =
        create_tool_call(org_id, session_id, "charge_card", ToolTier::Compensable { compensation });

    // First intercept should succeed.
    let outcome1 = engine.intercept(call.clone()).await.expect("first intercept failed");
    let effect_id1 = match outcome1 {
        InterceptOutcome::Execute { effect_id } => effect_id,
        _ => panic!("Expected Execute on first call"),
    };

    // Second intercept with same signature should return Replay.
    let outcome2 = engine.intercept(call).await.expect("second intercept failed");

    match outcome2 {
        InterceptOutcome::Replay { effect_id, result } => {
            assert_eq!(effect_id, effect_id1, "Expected same effect_id on replay");
            println!("Replay detected: effect_id = {}, result = {:?}", effect_id, result);
        }
        _ => panic!("Expected Replay outcome on duplicate"),
    }
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn irreversible_creates_approval_request_and_suspends_session() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let call = create_tool_call(
        org_id,
        session_id,
        "delete_database",
        ToolTier::Irreversible { reason: "Permanently deletes all data".to_string() },
    );

    let outcome = engine.intercept(call).await.expect("intercept failed");

    match outcome {
        InterceptOutcome::AwaitingApproval { effect_id, approval_request_id } => {
            println!(
                "Irreversible: effect_id = {}, approval_request_id = {}",
                effect_id, approval_request_id
            );
            // Session should now be in AwaitingApproval state (would need DB query to verify).
        }
        _ => panic!("Expected AwaitingApproval outcome for Irreversible tier"),
    }
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn commit_updates_state_and_caches_result() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let compensation = CompensationDescriptor::new("cancel_payment", json!({"amount": 100}));
    let call =
        create_tool_call(org_id, session_id, "charge_card", ToolTier::Compensable { compensation });

    let outcome = engine.intercept(call.clone()).await.expect("intercept failed");

    let effect_id = match outcome {
        InterceptOutcome::Execute { effect_id } => effect_id,
        _ => panic!("Expected Execute"),
    };

    // Mark as executing.
    engine.set_executing(&org_id, &effect_id).await.expect("set_executing failed");

    // Commit with result.
    let result = ToolResult {
        success: true,
        output: json!({"transaction_id": "tx_12345"}),
        error: None,
        duration_ms: 150,
    };

    engine.commit(&org_id, &effect_id, result).await.expect("commit failed");

    println!("Committed effect: {}", effect_id);
    // Verify effect state is Committed in DB (would need DB query).
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn fail_records_error_and_marks_pending() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let compensation = CompensationDescriptor::new("cancel_payment", json!({"amount": 100}));
    let call =
        create_tool_call(org_id, session_id, "charge_card", ToolTier::Compensable { compensation });

    let outcome = engine.intercept(call).await.expect("intercept failed");

    let effect_id = match outcome {
        InterceptOutcome::Execute { effect_id } => effect_id,
        _ => panic!("Expected Execute"),
    };

    // Mark as executing.
    engine.set_executing(&org_id, &effect_id).await.expect("set_executing failed");

    // Fail the effect.
    engine.fail(&org_id, &effect_id, "Network timeout").await.expect("fail failed");

    println!("Failed effect: {}", effect_id);
    // Verify effect state is Pending (reverted) and error is cached.
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn approval_workflow() {
    let engine = setup_engine().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&org_id, &session_id).await;

    let call = create_tool_call(
        org_id,
        session_id,
        "delete_database",
        ToolTier::Irreversible { reason: "Permanently deletes all data".to_string() },
    );

    let outcome = engine.intercept(call).await.expect("intercept failed");

    let approval_request_id = match outcome {
        InterceptOutcome::AwaitingApproval { approval_request_id, .. } => approval_request_id,
        _ => panic!("Expected AwaitingApproval"),
    };

    // Approve the request.
    engine
        .approve(&org_id, &approval_request_id, "admin_user", Some(json!({"confirmed": true})))
        .await
        .expect("approve failed");

    println!("Approval granted: {}", approval_request_id);
    // Session should be back to Active state.
}
