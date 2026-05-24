use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::Duration,
};

use chrono::Utc;
use proptest::prelude::*;
use serial_test::serial;
use undolog_saga::{
    compensation_runner::idempotency_key_for,
    orchestrator::{CompensationRegistry, RetryConfig, SagaError, SagaOrchestrator, SagaStore},
};
use undolog_types::{
    ids::{EffectId, OrgId, SessionId, UndoId},
    saga::{SagaStepState, UndoEntry},
    session::{SessionRecord, SessionState},
    tier::CompensationDescriptor,
};
use uuid::Uuid;
use wiremock::{
    matchers::{method, path},
    Mock, MockServer, ResponseTemplate,
};

#[derive(Clone)]
struct MockRegistry {
    endpoints: Arc<HashMap<String, String>>,
    retry: RetryConfig,
}

impl MockRegistry {
    fn new(endpoints: HashMap<String, String>) -> Self {
        Self {
            endpoints: Arc::new(endpoints),
            retry: RetryConfig {
                max_retries: 2,
                initial_interval: Duration::from_millis(1),
                multiplier: 2.0,
                max_interval: Duration::from_millis(10),
            },
        }
    }
}

impl CompensationRegistry for MockRegistry {
    fn endpoint_for(&self, key: &str) -> Option<String> {
        self.endpoints.get(key).cloned()
    }

    fn default_retry_config(&self) -> RetryConfig {
        self.retry
    }
}

#[derive(Clone)]
struct MockStore {
    session: SessionRecord,
    stack: Vec<UndoEntry>,
    compensated: Arc<Mutex<Vec<UndoId>>>,
    skipped: Arc<Mutex<Vec<UndoId>>>,
    halted: Arc<Mutex<Option<String>>>,
}

impl MockStore {
    fn new(session: SessionRecord, stack: Vec<UndoEntry>) -> Self {
        Self {
            session,
            stack,
            compensated: Arc::new(Mutex::new(Vec::new())),
            skipped: Arc::new(Mutex::new(Vec::new())),
            halted: Arc::new(Mutex::new(None)),
        }
    }

    fn compensated_order(&self) -> Vec<UndoId> {
        self.compensated.lock().expect("mutex").clone()
    }
}

#[async_trait::async_trait]
impl SagaStore for MockStore {
    async fn load_session(
        &self,
        _org_id: OrgId,
        _session_id: SessionId,
    ) -> Result<Option<SessionRecord>, SagaError> {
        Ok(Some(self.session.clone()))
    }

    async fn load_undo_stack(
        &self,
        _org_id: OrgId,
        _session_id: SessionId,
    ) -> Result<Vec<UndoEntry>, SagaError> {
        Ok(self.stack.clone())
    }

    async fn mark_entry_compensated(
        &self,
        _org_id: OrgId,
        undo_id: UndoId,
    ) -> Result<(), SagaError> {
        self.compensated.lock().expect("mutex").push(undo_id);
        Ok(())
    }

    async fn mark_entry_compensation_failed(
        &self,
        _org_id: OrgId,
        undo_id: UndoId,
        _reason: &str,
    ) -> Result<(), SagaError> {
        self.skipped.lock().expect("mutex").push(undo_id);
        Ok(())
    }

    async fn mark_session_compensated(
        &self,
        _org_id: OrgId,
        _session_id: SessionId,
    ) -> Result<(), SagaError> {
        Ok(())
    }

    async fn mark_session_halted(
        &self,
        _org_id: OrgId,
        _session_id: SessionId,
        reason: &str,
    ) -> Result<(), SagaError> {
        *self.halted.lock().expect("mutex") = Some(reason.to_string());
        Ok(())
    }

    async fn mark_effect_compensation_failed(
        &self,
        _org_id: OrgId,
        _effect_id: EffectId,
    ) -> Result<(), SagaError> {
        Ok(())
    }
}

fn session_record(org_id: OrgId, session_id: SessionId) -> SessionRecord {
    SessionRecord {
        session_id,
        org_id,
        project_id: None,
        external_run_id: None,
        agent_name: Some("test-agent".to_string()),
        state: SessionState::Failed,
        tool_calls_total: 0,
        compensations_total: 0,
        approvals_pending: 0,
        started_at: Utc::now(),
        completed_at: None,
        failed_at: None,
        failure_reason: Some("boom".to_string()),
        metadata: serde_json::json!({}),
    }
}

fn entry(
    org_id: OrgId,
    session_id: SessionId,
    stack_position: u32,
    fn_name: String,
    state: SagaStepState,
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
            max_retries: 0,
            retry_backoff_ms: 1,
        },
        state,
        retry_count: 0,
        last_error: None,
        registered_at: Utc::now(),
        compensated_at: None,
    }
}

async fn build_orchestrator(
    entries: Vec<UndoEntry>,
) -> (SagaOrchestrator, Arc<MockStore>, MockServer) {
    let org_id = entries[0].org_id;
    let session_id = entries[0].session_id;
    let server = MockServer::start().await;

    let mut endpoints = HashMap::new();
    for item in &entries {
        endpoints.insert(
            item.compensation.fn_name.clone(),
            format!("{}/{}", server.uri(), item.compensation.fn_name),
        );
        Mock::given(method("POST"))
            .and(path(format!("/{}", item.compensation.fn_name)))
            .respond_with(ResponseTemplate::new(200))
            .mount(&server)
            .await;
    }

    let store = Arc::new(MockStore::new(session_record(org_id, session_id), entries));
    let registry = Arc::new(MockRegistry::new(endpoints));
    let orchestrator = SagaOrchestrator::new(store.clone(), registry);

    (orchestrator, store, server)
}

proptest! {
    #[test]
    fn compensates_entries_in_store_order(values in prop::collection::vec(1u32..1000, 1..6).prop_filter("unique values", |vals| {
        let mut seen = std::collections::HashSet::new();
        vals.iter().all(|value| seen.insert(*value))
    })) {
        let rt = tokio::runtime::Runtime::new().expect("runtime");
        rt.block_on(async move {
            let org_id = OrgId::new();
            let session_id = SessionId::new();
            let entries = values
                .into_iter()
                .enumerate()
                .map(|(index, value)| {
                    entry(
                        org_id,
                        session_id,
                        index as u32,
                        format!("comp-{}", value),
                        SagaStepState::Pending,
                    )
                })
                .collect::<Vec<_>>();

            let (orchestrator, store, _server) = build_orchestrator(entries.clone()).await;
            let status = orchestrator.compensate_session(org_id, session_id).await.expect("orchestration");

            assert_eq!(status, SessionState::Compensated);
            assert_eq!(store.compensated_order(), entries.iter().map(|entry| entry.undo_id).collect::<Vec<_>>());
        });
    }

    #[test]
    fn idempotency_key_is_derived_from_undo_id(bytes in any::<[u8; 16]>()) {
        let undo_id = UndoId::from(Uuid::from_bytes(bytes));
        prop_assert_eq!(idempotency_key_for(&undo_id), format!("undo-{undo_id}"));
    }
}

#[tokio::test]
#[serial]
async fn skips_already_compensated_entries() {
    let org_id = OrgId::new();
    let session_id = SessionId::new();
    let first = entry(org_id, session_id, 0, "comp-first".to_string(), SagaStepState::Compensated);
    let second = entry(org_id, session_id, 1, "comp-second".to_string(), SagaStepState::Pending);

    let (orchestrator, store, _server) =
        build_orchestrator(vec![first.clone(), second.clone()]).await;
    let status = orchestrator.compensate_session(org_id, session_id).await.expect("orchestration");

    assert_eq!(status, SessionState::Compensated);
    assert_eq!(store.compensated_order(), vec![second.undo_id]);
}
