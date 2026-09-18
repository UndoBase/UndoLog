//! Crash recovery integration tests.
//!
//! These tests verify that the saga orchestrator can recover from a process
//! crash during compensation. They start the engine binary, inject a fault,
//! kill the process, restart, and verify the compensation completes.
//!
//! Requires:
//! - `TEST_DATABASE_URL` environment variable
//! - `undolog-engine` binary built with `cargo build -p undolog-engine`

use std::{
    process::{Child, Command},
    time::Duration,
};

use sqlx::{postgres::PgPoolOptions, PgPool};
use tokio::time::sleep;
use undolog_types::ids::{OrgId, SessionId};

// ── Constants ──────────────────────────────────────────────────────────────

const ENGINE_BINARY: &str = "undolog-engine";
const GRPC_PORT: u16 = 50099;
const HEALTH_PORT: u16 = 9099;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(10);

// ── Test helpers ───────────────────────────────────────────────────────────

fn test_database_url() -> String {
    std::env::var("TEST_DATABASE_URL")
        .expect("TEST_DATABASE_URL must be set for crash recovery tests")
}

async fn pool() -> PgPool {
    PgPoolOptions::new()
        .max_connections(5)
        .connect(&test_database_url())
        .await
        .expect("database connection")
}

async fn insert_org(pool: &PgPool, org_id: OrgId) {
    let slug = format!("crash-test-{}", org_id.as_uuid());
    sqlx::query(
        r#"
        INSERT INTO undolog_orgs (org_id, name, slug)
        VALUES ($1, $2, $3)
        ON CONFLICT (org_id) DO NOTHING
        "#,
    )
    .bind(*org_id.as_uuid())
    .bind(&slug)
    .bind(&slug)
    .execute(pool)
    .await
    .expect("insert org");
}

async fn insert_session(pool: &PgPool, session_id: SessionId, org_id: OrgId) {
    sqlx::query(
        r#"
        INSERT INTO undolog_sessions (
            session_id, org_id, state, started_at, metadata
        )
        VALUES ($1, $2, 'compensating'::undolog_session_state, now(), $3)
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(*session_id.as_uuid())
    .bind(*org_id.as_uuid())
    .bind(serde_json::json!({"test": "crash_recovery"}))
    .execute(pool)
    .await
    .expect("insert session");
}

async fn insert_pending_effect(pool: &PgPool, org_id: OrgId, session_id: SessionId) {
    let effect_id = uuid::Uuid::new_v4();
    let undo_id = uuid::Uuid::new_v4();
    let call_signature = format!("{:032x}{:032x}", 0, effect_id.as_u128());

    sqlx::query(
        r#"
        INSERT INTO undolog_effect_log (
            effect_id, org_id, session_id, tool_id,
            call_signature, tool_name, tool_version, tier,
            step_index, args_snapshot, state,
            compensation_args, executed_at
        )
        VALUES (
            $1, $2, $3, NULL,
            $4, 'test_tool', '1.0.0', 'compensable'::undolog_tool_tier,
            0, '{}', 'pending'::undolog_effect_state,
            '{"key": "value"}', now()
        )
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(effect_id)
    .bind(*org_id.as_uuid())
    .bind(*session_id.as_uuid())
    .bind(&call_signature)
    .execute(pool)
    .await
    .expect("insert effect");

    sqlx::query(
        r#"
        INSERT INTO undolog_undo_stack (
            undo_id, org_id, session_id, effect_id,
            stack_position, compensation_fn, compensation_version,
            compensation_args, state, registered_at
        )
        VALUES ($1, $2, $3, $4, 0, 'compensate_test', '1.0.0', $5, 'pending', now())
        ON CONFLICT DO NOTHING
        "#,
    )
    .bind(undo_id)
    .bind(*org_id.as_uuid())
    .bind(*session_id.as_uuid())
    .bind(effect_id)
    .bind(serde_json::json!({"key": "value"}))
    .execute(pool)
    .await
    .expect("insert undo entry");
}

fn start_engine() -> Child {
    Command::new(ENGINE_BINARY)
        .env("DATABASE_URL", test_database_url())
        .env("UNDOLOG_ENGINE_GRPC_ADDR", format!("0.0.0.0:{GRPC_PORT}"))
        .env("UNDOLOG_ENGINE_HEALTH_ADDR", format!("0.0.0.0:{HEALTH_PORT}"))
        .env("RUST_LOG", "info")
        .spawn()
        .expect("failed to start engine")
}

async fn wait_for_engine_ready() {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{HEALTH_PORT}/health");

    for _ in 0..20 {
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => return,
            _ => sleep(Duration::from_millis(500)).await,
        }
    }
    panic!("engine did not become ready within {STARTUP_TIMEOUT:?}");
}

fn kill_engine(child: &mut Child) {
    child.kill().expect("failed to kill engine");
    child.wait().expect("failed to wait for engine");
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL and built engine binary"]
async fn crash_recovery_resumes_compensation() {
    let org_id = OrgId::new();
    let session_id = SessionId::new();

    // Setup database state.
    let db_pool = pool().await;
    insert_org(&db_pool, org_id).await;
    insert_session(&db_pool, session_id, org_id).await;
    insert_pending_effect(&db_pool, org_id, session_id).await;

    // Start engine and wait for readiness.
    let mut engine = start_engine();
    wait_for_engine_ready().await;

    // Simulate crash by killing engine.
    kill_engine(&mut engine);

    // Verify session is still in compensating state (crash happened before completion).
    let state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_sessions WHERE session_id = $1")
            .bind(*session_id.as_uuid())
            .fetch_one(&db_pool)
            .await
            .expect("query session state");

    assert_eq!(state, "compensating", "session should still be compensating after crash");

    // Restart engine.
    let mut engine2 = start_engine();
    wait_for_engine_ready().await;

    // Give orchestrator time to resume and complete.
    sleep(Duration::from_secs(2)).await;

    // Verify session reached a terminal state.
    let final_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_sessions WHERE session_id = $1")
            .bind(*session_id.as_uuid())
            .fetch_one(&db_pool)
            .await
            .expect("query final session state");

    assert!(
        final_state == "compensated" || final_state == "halted",
        "session should be compensated or halted after recovery, got: {final_state}"
    );

    // Cleanup.
    kill_engine(&mut engine2);
}
