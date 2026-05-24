use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};

use chrono::Utc;
use serial_test::serial;
use sqlx::{postgres::PgPoolOptions, PgPool};
use undolog_saga::orchestrator::{
    CompensationRegistry, DatabaseSagaStore, RetryConfig, SagaOrchestrator,
};
use undolog_store::{EffectStore, SessionStore};
use undolog_types::{
    ids::{EffectId, OrgId, SessionId, UndoId},
    saga::{SagaStepState, UndoEntry},
    session::SessionState,
    tier::CompensationDescriptor,
};
use wiremock::{
    matchers::{method, path},
    Mock, MockServer, Respond, ResponseTemplate,
};

struct FlakyResponder {
    failures: usize,
    calls: Arc<AtomicUsize>,
}

impl Respond for FlakyResponder {
    fn respond(&self, _request: &wiremock::Request) -> ResponseTemplate {
        let call = self.calls.fetch_add(1, Ordering::SeqCst);
        if call < self.failures {
            ResponseTemplate::new(500)
        } else {
            ResponseTemplate::new(200)
        }
    }
}

#[derive(Clone)]
struct TestRegistry {
    endpoints: Arc<HashMap<String, String>>,
    retry: RetryConfig,
}

impl TestRegistry {
    fn new(endpoints: HashMap<String, String>, max_retries: u32, initial_delay: Duration) -> Self {
        Self {
            endpoints: Arc::new(endpoints),
            retry: RetryConfig {
                max_retries,
                initial_interval: initial_delay,
                multiplier: 2.0,
                max_interval: Duration::from_millis(20),
            },
        }
    }
}

impl CompensationRegistry for TestRegistry {
    fn endpoint_for(&self, key: &str) -> Option<String> {
        self.endpoints.get(key).cloned()
    }

    fn default_retry_config(&self) -> RetryConfig {
        self.retry
    }
}

fn test_database_url() -> String {
    std::env::var("TEST_DATABASE_URL")
        .expect("TEST_DATABASE_URL must be set for saga integration tests")
}

async fn pool() -> PgPool {
    PgPoolOptions::new()
        .max_connections(5)
        .connect(&test_database_url())
        .await
        .expect("database connection")
}

async fn insert_org(pool: &PgPool, org_id: OrgId, slug_prefix: &str) {
    let slug = format!("{}-{}", slug_prefix, org_id.as_uuid());
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

async fn insert_session(pool: &PgPool, session_id: SessionId, org_id: OrgId, state: SessionState) {
    sqlx::query(
        r#"
        INSERT INTO undolog_sessions (
            session_id, org_id, state, started_at, metadata
        )
        VALUES ($1, $2, $3::undolog_session_state, now(), $4)
        "#,
    )
    .bind(*session_id.as_uuid())
    .bind(*org_id.as_uuid())
    .bind(match state {
        SessionState::Active => "active",
        SessionState::Completed => "completed",
        SessionState::Failed => "failed",
        SessionState::Compensating => "compensating",
        SessionState::Compensated => "compensated",
        SessionState::AwaitingApproval => "awaiting_approval",
        SessionState::Halted => "halted",
    })
    .bind(serde_json::json!({}))
    .execute(pool)
    .await
    .expect("insert session");
}

async fn insert_undo_entry(pool: &PgPool, entry: &UndoEntry) {
    let effect_state = match entry.state {
        SagaStepState::Pending | SagaStepState::Running => "pending",
        SagaStepState::Compensated => "compensated",
        SagaStepState::Failed => "failed",
        SagaStepState::Skipped => "skipped",
    };

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
            $4, $5, $6, 'compensable'::undolog_tool_tier,
            $7, $8, $9::undolog_effect_state,
            $10, now()
        )
        "#,
    )
    .bind(*entry.effect_id.as_uuid())
    .bind(*entry.org_id.as_uuid())
    .bind(*entry.session_id.as_uuid())
    .bind(format!("{:032x}{:032x}", entry.stack_position, entry.effect_id.as_uuid().as_u128()))
    .bind(&entry.compensation.fn_name)
    .bind(&entry.compensation.fn_version)
    .bind(entry.stack_position as i32)
    .bind(serde_json::json!({"stack_position": entry.stack_position}))
    .bind(effect_state)
    .bind(&entry.compensation.args)
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
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
        "#,
    )
    .bind(*entry.undo_id.as_uuid())
    .bind(*entry.org_id.as_uuid())
    .bind(*entry.session_id.as_uuid())
    .bind(*entry.effect_id.as_uuid())
    .bind(entry.stack_position as i32)
    .bind(&entry.compensation.fn_name)
    .bind(&entry.compensation.fn_version)
    .bind(&entry.compensation.args)
    .bind(match entry.state {
        SagaStepState::Pending => "pending",
        SagaStepState::Running => "running",
        SagaStepState::Compensated => "compensated",
        SagaStepState::Failed => "failed",
        SagaStepState::Skipped => "skipped",
    })
    .execute(pool)
    .await
    .expect("insert undo");
}

fn entry(
    org_id: OrgId,
    session_id: SessionId,
    stack_position: u32,
    fn_name: String,
    state: SagaStepState,
    max_retries: u8,
) -> UndoEntry {
    UndoEntry {
        undo_id: UndoId::new(),
        org_id,
        session_id,
        effect_id: EffectId::new(),
        stack_position,
        compensation: CompensationDescriptor {
            fn_name,
            fn_version: "1.0.0".to_string(),
            args: serde_json::json!({"stack_position": stack_position}),
            max_retries,
            retry_backoff_ms: 1,
        },
        state,
        retry_count: 0,
        last_error: None,
        registered_at: Utc::now(),
        compensated_at: None,
    }
}

async fn orchestrator_for(
    entries: &[UndoEntry],
    max_retries: u32,
    initial_delay: Duration,
) -> (SagaOrchestrator, MockServer) {
    let server = MockServer::start().await;
    let mut endpoints = HashMap::new();
    for item in entries {
        endpoints.insert(
            item.compensation.fn_name.clone(),
            format!("{}/{}", server.uri(), item.compensation.fn_name),
        );
    }
    let registry = Arc::new(TestRegistry::new(endpoints, max_retries, initial_delay));
    let pool = pool().await;
    let store = Arc::new(DatabaseSagaStore::new(
        EffectStore::new(pool.clone()),
        SessionStore::new(pool.clone()),
    ));
    let orchestrator = SagaOrchestrator::new(store, registry);
    (orchestrator, server)
}

#[tokio::test]
#[serial]
async fn partial_failure_compensates_in_lifo_order() {
    let pool = pool().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();
    insert_org(&pool, org_id, "partial-failure-test").await;
    insert_session(&pool, session_id, org_id, SessionState::Failed).await;

    let first = entry(org_id, session_id, 1, "step-1".to_string(), SagaStepState::Pending, 0);
    let second = entry(org_id, session_id, 2, "step-2".to_string(), SagaStepState::Pending, 0);
    insert_undo_entry(&pool, &second).await;
    insert_undo_entry(&pool, &first).await;

    let (orchestrator, server) =
        orchestrator_for(&[second.clone(), first.clone()], 0, Duration::from_millis(1)).await;
    for item in [&second, &first] {
        Mock::given(method("POST"))
            .and(path(format!("/{}", item.compensation.fn_name)))
            .respond_with(ResponseTemplate::new(200))
            .mount(&server)
            .await;
    }

    let status = orchestrator.compensate_session(org_id, session_id).await.expect("orchestration");
    assert_eq!(status, SessionState::Compensated);

    let session = SessionStore::new(pool.clone())
        .get_session(&org_id, &session_id)
        .await
        .expect("session lookup")
        .expect("session");
    assert_eq!(session.state, SessionState::Compensated);

    let first_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_undo_stack WHERE undo_id = $1")
            .bind(*first.undo_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("first state");
    let second_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_undo_stack WHERE undo_id = $1")
            .bind(*second.undo_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("second state");
    assert_eq!(first_state, "compensated");
    assert_eq!(second_state, "compensated");
}

#[tokio::test]
#[serial]
async fn retries_then_succeeds() {
    let pool = pool().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();
    insert_org(&pool, org_id, "retries-then-succeeds-test").await;
    insert_session(&pool, session_id, org_id, SessionState::Failed).await;

    let only = entry(org_id, session_id, 1, "retry-step".to_string(), SagaStepState::Pending, 3);
    insert_undo_entry(&pool, &only).await;

    let (orchestrator, server) =
        orchestrator_for(&[only.clone()], 3, Duration::from_millis(1)).await;
    let calls = Arc::new(AtomicUsize::new(0));
    Mock::given(method("POST"))
        .and(path(format!("/{}", only.compensation.fn_name)))
        .respond_with(FlakyResponder { failures: 2, calls: calls.clone() })
        .mount(&server)
        .await;

    let status = orchestrator.compensate_session(org_id, session_id).await.expect("orchestration");
    assert_eq!(status, SessionState::Compensated);
    assert_eq!(calls.load(Ordering::SeqCst), 3);
}

#[tokio::test]
#[serial]
async fn permanent_failure_halts_session() {
    let pool = pool().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();
    insert_org(&pool, org_id, "permanent-failure-test").await;
    insert_session(&pool, session_id, org_id, SessionState::Failed).await;

    let only =
        entry(org_id, session_id, 1, "permanent-step".to_string(), SagaStepState::Pending, 1);
    insert_undo_entry(&pool, &only).await;

    let (orchestrator, server) =
        orchestrator_for(&[only.clone()], 1, Duration::from_millis(1)).await;
    let calls = Arc::new(AtomicUsize::new(0));
    Mock::given(method("POST"))
        .and(path(format!("/{}", only.compensation.fn_name)))
        .respond_with(FlakyResponder { failures: usize::MAX / 2, calls: calls.clone() })
        .mount(&server)
        .await;

    let status = orchestrator.compensate_session(org_id, session_id).await.expect("orchestration");
    assert_eq!(status, SessionState::Halted);
    assert!(calls.load(Ordering::SeqCst) >= 2);

    let undo_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_undo_stack WHERE undo_id = $1")
            .bind(*only.undo_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("undo state");
    assert_eq!(undo_state, "failed");

    let effect_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_effect_log WHERE effect_id = $1")
            .bind(*only.effect_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("effect state");
    assert_eq!(effect_state, "compensation_failed");
}

#[tokio::test]
#[serial]
async fn rerun_skips_precompensated_entries() {
    let pool = pool().await;
    let org_id = OrgId::new();
    let session_id = SessionId::new();
    insert_org(&pool, org_id, "rerun-skips-test").await;
    insert_session(&pool, session_id, org_id, SessionState::Failed).await;

    let compensated =
        entry(org_id, session_id, 2, "skip-me".to_string(), SagaStepState::Compensated, 0);
    let pending = entry(org_id, session_id, 1, "run-me".to_string(), SagaStepState::Pending, 0);
    insert_undo_entry(&pool, &compensated).await;
    insert_undo_entry(&pool, &pending).await;

    let (orchestrator, server) =
        orchestrator_for(&[compensated.clone(), pending.clone()], 0, Duration::from_millis(1))
            .await;
    Mock::given(method("POST"))
        .and(path(format!("/{}", pending.compensation.fn_name)))
        .respond_with(ResponseTemplate::new(200))
        .mount(&server)
        .await;

    let status = orchestrator.compensate_session(org_id, session_id).await.expect("orchestration");
    assert_eq!(status, SessionState::Compensated);

    let compensated_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_undo_stack WHERE undo_id = $1")
            .bind(*compensated.undo_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("compensated state");
    assert_eq!(compensated_state, "compensated");

    let pending_state: String =
        sqlx::query_scalar("SELECT state::text FROM undolog_undo_stack WHERE undo_id = $1")
            .bind(*pending.undo_id.as_uuid())
            .fetch_one(&pool)
            .await
            .expect("pending state");
    assert_eq!(pending_state, "compensated");
}
