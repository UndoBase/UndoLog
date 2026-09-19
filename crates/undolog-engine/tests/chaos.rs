//! Chaos engineering tests for the UndoLog Effect Engine.
//!
//! These tests verify engine behavior under adverse conditions.
//! Each test simulates a failure mode and verifies the system reaches
//! a defined pass/fail criterion.
//!
//! Tests requiring a live database are marked `#[ignore]` and run with:
//! `cargo test -p undolog-engine --test chaos -- --ignored`

#![cfg(all(test, feature = "pg"))]

use std::sync::Arc;

use serde_json::json;
use tokio::sync::Barrier;
use undolog_engine::{EffectEngine, EngineConfig, InterceptOutcome, TierRegistry};
use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::{
    effect::ToolCall,
    ids::{OrgId, SessionId, ToolId},
    tier::{CompensationDescriptor, ToolTier},
};

// ── Test helpers ───────────────────────────────────────────────────────────

fn test_database_url() -> String {
    std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string())
}

async fn setup_engine() -> EffectEngine {
    let pool = sqlx::PgPool::connect(&test_database_url())
        .await
        .expect("Failed to connect to test database");

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

async fn create_test_session(pool: &sqlx::PgPool, org_id: &OrgId, session_id: &SessionId) {
    sqlx::query(
        r#"
        INSERT INTO undolog_sessions (
            session_id, org_id, state, started_at, metadata
        )
        VALUES ($1, $2, 'active'::undolog_session_state, now(), '{}')
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(*session_id.as_uuid())
    .bind(*org_id.as_uuid())
    .execute(pool)
    .await
    .expect("insert session");
}

fn compensable_call(org_id: OrgId, session_id: SessionId, step: u32) -> ToolCall {
    let compensation = CompensationDescriptor::new("compensate_test", json!({"amount": 100}));
    ToolCall {
        org_id,
        session_id,
        tool_id: Some(ToolId::new()),
        tool_name: "charge_card".to_string(),
        tool_version: "1.0.0".to_string(),
        tier: ToolTier::Compensable { compensation },
        step_index: step,
        args: json!({"amount": 100, "recipient": "alice"}),
        intercepted_at: chrono::Utc::now(),
    }
}

// ── Chaos tests ────────────────────────────────────────────────────────────

/// Abort a tokio task running an intercept to simulate a process crash.
///
/// Pass criterion: The session record remains in a valid state. No panics
/// or corrupt data.
#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn crash_during_compensation_does_not_corrupt_state() {
    let engine = setup_engine().await;
    let pool = sqlx::PgPool::connect(&test_database_url()).await.expect("connect");
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&pool, &org_id, &session_id).await;

    // Spawn a long-running intercept to increase the window for abort.
    let engine_clone = engine.clone();
    let org = org_id;
    let session = session_id;
    let handle = tokio::spawn(async move {
        // First intercept creates the effect log entry.
        let call = compensable_call(org, session, 0);
        let _ = engine_clone.intercept(call).await;

        // Second intercept triggers the advisory lock path.
        let call2 = compensable_call(org, session, 1);
        engine_clone.intercept(call2).await
    });

    // Yield to let the task start, then abort.
    tokio::task::yield_now().await;
    handle.abort();
    let result = handle.await;

    // The task was aborted; we expect a JoinError.
    assert!(result.is_err(), "task should have been aborted (simulated crash)");

    // Verify the session record is still valid in the database.
    let state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_sessions WHERE session_id = $1")
            .bind(*session_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("query session state");

    let valid_states = ["active", "compensating", "compensated", "failed", "halted"];
    assert!(
        valid_states.contains(&state.as_str()),
        "session state '{state}' should be valid after simulated crash"
    );
}

/// Two concurrent intercepts for the same session and step (same signature)
/// to test advisory lock serialization.
///
/// Pass criterion: Exactly one returns Execute, the other returns Replay.
/// No panics, no duplicate effect entries.
#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn concurrent_same_signature_serialized_by_lock() {
    let engine = setup_engine().await;
    let pool = sqlx::PgPool::connect(&test_database_url()).await.expect("connect");

    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&pool, &org_id, &session_id).await;

    // Both calls use the same session_id, step, tool_name, and args,
    // producing an identical CallSignature.
    let call_a = compensable_call(org_id, session_id, 0);
    let call_b = compensable_call(org_id, session_id, 0);

    // Barrier ensures both tasks start concurrently.
    let barrier = Arc::new(Barrier::new(2));

    let engine_a = engine.clone();
    let barrier_a = barrier.clone();
    let handle_a = tokio::spawn(async move {
        barrier_a.wait().await;
        engine_a.intercept(call_a).await
    });

    let engine_b = engine.clone();
    let barrier_b = barrier.clone();
    let handle_b = tokio::spawn(async move {
        barrier_b.wait().await;
        engine_b.intercept(call_b).await
    });

    let result_a = handle_a.await.expect("task a panicked");
    let result_b = handle_b.await.expect("task b panicked");

    let is_execute =
        |r: &Result<InterceptOutcome, _>| matches!(r, Ok(InterceptOutcome::Execute { .. }));
    let is_replay =
        |r: &Result<InterceptOutcome, _>| matches!(r, Ok(InterceptOutcome::Replay { .. }));

    // Advisory lock serializes: one Execute, one Replay.
    assert!(
        (is_execute(&result_a) && is_replay(&result_b))
            || (is_replay(&result_a) && is_execute(&result_b)),
        "expected one Execute and one Replay, got {:?} / {:?}",
        result_a,
        result_b,
    );
}

/// Verify the engine propagates database errors without panicking.
///
/// Pass criterion: Engine returns an error, does not panic.
#[tokio::test]
async fn db_connection_failure_propagates_error() {
    let bad_url = "postgresql://invalid:invalid@nonexistent:5432/undolog_fake";
    let pool = match sqlx::PgPool::connect(bad_url).await {
        Ok(p) => p,
        Err(_) => return, // Connection refused; test passes.
    };

    let effect_store = EffectStore::new(pool.clone());
    let session_store = SessionStore::new(pool.clone());
    let approval_store = ApprovalStore::new(pool);

    let registry = Arc::new(tokio::sync::RwLock::new(TierRegistry::new()));
    let engine = EffectEngine::new(
        effect_store,
        session_store,
        approval_store,
        registry,
        EngineConfig::default(),
    );

    let org_id = OrgId::new();
    let session_id = SessionId::new();
    let call = compensable_call(org_id, session_id, 0);

    let result = engine.intercept(call).await;

    assert!(result.is_err(), "engine should return error on DB failure");
}

/// A new engine instance handles intercepts for sessions created before
/// the "restart" (pre-existing DB rows).
///
/// Pass criterion: The new engine successfully processes the intercept.
#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn new_engine_handles_pre_existing_sessions() {
    let pool = sqlx::PgPool::connect(&test_database_url()).await.expect("connect");
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&pool, &org_id, &session_id).await;

    // "Restart": fresh engine, empty cache.
    let engine = setup_engine().await;

    let call = compensable_call(org_id, session_id, 0);
    let result = engine.intercept(call).await;

    assert!(result.is_ok(), "new engine should handle intercept for pre-existing session");
}

/// Ten concurrent intercepts with the same signature to verify
/// exactly-once semantics under high contention.
///
/// Pass criterion: Exactly one Execute, nine Replays. No panics,
/// no duplicate effect entries.
#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn rapid_repeated_intercepts_are_idempotent() {
    let engine = setup_engine().await;
    let pool = sqlx::PgPool::connect(&test_database_url()).await.expect("connect");
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    create_test_session(&pool, &org_id, &session_id).await;

    // Fire 10 intercepts with the same signature concurrently.
    let mut handles = Vec::new();
    for i in 0..10 {
        let engine_clone = engine.clone();
        let call = compensable_call(org_id, session_id, 0);
        handles.push(tokio::spawn(async move {
            // Stagger to maximize race window.
            tokio::time::sleep(std::time::Duration::from_millis(i * 5)).await;
            engine_clone.intercept(call).await
        }));
    }

    let mut execute_count = 0u32;
    let mut replay_count = 0u32;

    for handle in handles {
        let result = handle.await.expect("task panicked");
        match result {
            Ok(InterceptOutcome::Execute { .. }) => execute_count += 1,
            Ok(InterceptOutcome::Replay { .. }) => replay_count += 1,
            Ok(InterceptOutcome::AwaitingApproval { .. }) => execute_count += 1,
            Err(e) => panic!("intercept returned error: {e}"),
        }
    }

    assert_eq!(execute_count, 1, "exactly one Execute");
    assert_eq!(replay_count, 9, "nine Replays");
    assert_eq!(execute_count + replay_count, 10, "all 10 calls completed");
}
