//! Integration and unit tests for dead-letter gRPC RPCs.
//!
//! Verifies:
//! - Unimplemented error when no dead-letter store is configured (e.g. SQLite).
//! - Input validation (invalid UUIDs return `InvalidArgument`).
//! - List filtering by state and organization.
//! - Retry transition from `failed` to `retrying` with retry count increment.
//! - Skip transition from `failed` to `skipped`.
//! - Rejection of retry/skip when record is not in `failed` state.
//! - Cross-organization isolation (returns `NotFound`).

#![cfg(all(test, feature = "pg"))]

use std::sync::Arc;
use tonic::{Code, Request};
use undolog_engine::grpc::pb::undo_log_engine_server::UndoLogEngine;
use undolog_engine::grpc::{pb, UndoLogEngineService};
use undolog_engine::{EffectEngine, EngineConfig, TierRegistry};
use undolog_store::{
    dead_letter::{CreateDeadLetterParams, DeadLetterStore},
    ApprovalStore, EffectStore, SessionStore,
};
use undolog_types::ids::{EffectId, OrgId, SessionId, UndoId};

fn mock_pool() -> sqlx::PgPool {
    sqlx::PgPool::connect_lazy("postgresql://localhost/test").unwrap()
}

fn mock_engine() -> EffectEngine {
    let pool = mock_pool();
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

// ── Unit Tests: Unimplemented (SQLite / unconfigured backend) ───────────────

#[tokio::test]
async fn test_dead_letter_rpcs_unimplemented_when_store_is_none() {
    let service = UndoLogEngineService::new(mock_engine(), None);
    let org_id = uuid::Uuid::new_v4().to_string();
    let dead_letter_id = uuid::Uuid::new_v4().to_string();

    // ListDeadLetters
    let list_req = Request::new(pb::ListDeadLettersRequest {
        org_id: org_id.clone(),
        state: pb::DeadLetterState::Failed as i32,
    });
    let list_err = service.list_dead_letters(list_req).await.unwrap_err();
    assert_eq!(list_err.code(), Code::Unimplemented);
    assert!(list_err.message().contains("not supported"));

    // RetryDeadLetter
    let retry_req = Request::new(pb::RetryDeadLetterRequest {
        org_id: org_id.clone(),
        dead_letter_id: dead_letter_id.clone(),
    });
    let retry_err = service.retry_dead_letter(retry_req).await.unwrap_err();
    assert_eq!(retry_err.code(), Code::Unimplemented);
    assert!(retry_err.message().contains("not supported"));

    // SkipDeadLetter
    let skip_req = Request::new(pb::SkipDeadLetterRequest { org_id, dead_letter_id });
    let skip_err = service.skip_dead_letter(skip_req).await.unwrap_err();
    assert_eq!(skip_err.code(), Code::Unimplemented);
    assert!(skip_err.message().contains("not supported"));
}

// ── Unit Tests: Argument Validation ────────────────────────────────────────

#[tokio::test]
async fn test_dead_letter_invalid_argument_validation() {
    let store = DeadLetterStore::new(mock_pool());
    let service = UndoLogEngineService::new(mock_engine(), Some(store));
    let valid_id = uuid::Uuid::new_v4().to_string();

    // Invalid org_id in ListDeadLetters
    let err = service
        .list_dead_letters(Request::new(pb::ListDeadLettersRequest {
            org_id: "not-a-valid-uuid".to_string(),
            state: pb::DeadLetterState::Failed as i32,
        }))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);

    // Invalid org_id in RetryDeadLetter
    let err = service
        .retry_dead_letter(Request::new(pb::RetryDeadLetterRequest {
            org_id: "not-a-valid-uuid".to_string(),
            dead_letter_id: valid_id.clone(),
        }))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);

    // Invalid dead_letter_id in RetryDeadLetter
    let err = service
        .retry_dead_letter(Request::new(pb::RetryDeadLetterRequest {
            org_id: valid_id.clone(),
            dead_letter_id: "malformed-id".to_string(),
        }))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);

    // Invalid dead_letter_id in SkipDeadLetter
    let err = service
        .skip_dead_letter(Request::new(pb::SkipDeadLetterRequest {
            org_id: valid_id,
            dead_letter_id: "malformed-id".to_string(),
        }))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);
}

// ── Integration Tests: Real Database Transitions ───────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn test_dead_letter_grpc_lifecycle_integration() {
    let db_url = std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string());
    let pool = sqlx::PgPool::connect(&db_url).await.expect("Failed to connect to test db");

    let store = DeadLetterStore::new(pool.clone());
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
    let service = UndoLogEngineService::new(engine, Some(store.clone()));

    let org1 = OrgId::new();
    let org2 = OrgId::new();
    let session_id = SessionId::new();

    // Create a dead letter record for org1
    let dl1 = store
        .create(CreateDeadLetterParams {
            org_id: org1,
            session_id,
            effect_id: EffectId::new(),
            undo_id: UndoId::new(),
            compensation_fn: "refund_order".to_string(),
            compensation_version: "1.0.0".to_string(),
            compensation_args: serde_json::json!({ "order_id": 123 }),
            error_message: "upstream gateway timeout".to_string(),
            retry_count: 0,
        })
        .await
        .expect("Failed to create dead letter");

    let dl1_id_str = dl1.dead_letter_id.to_string();

    // 1. List for org1 should include dl1
    let list_res = service
        .list_dead_letters(Request::new(pb::ListDeadLettersRequest {
            org_id: org1.to_string(),
            state: pb::DeadLetterState::Failed as i32,
        }))
        .await
        .expect("list failed")
        .into_inner();

    assert!(list_res.records.iter().any(|r| r.dead_letter_id == dl1_id_str));

    // 2. Cross-org listing for org2 should NOT include dl1
    let list_org2 = service
        .list_dead_letters(Request::new(pb::ListDeadLettersRequest {
            org_id: org2.to_string(),
            state: pb::DeadLetterState::Failed as i32,
        }))
        .await
        .expect("list failed")
        .into_inner();

    assert!(!list_org2.records.iter().any(|r| r.dead_letter_id == dl1_id_str));

    // 3. Cross-org retry should return NotFound
    let cross_retry_err = service
        .retry_dead_letter(Request::new(pb::RetryDeadLetterRequest {
            org_id: org2.to_string(),
            dead_letter_id: dl1_id_str.clone(),
        }))
        .await
        .unwrap_err();
    assert_eq!(cross_retry_err.code(), Code::NotFound);

    // 4. Same-org retry succeeds and transitions failed -> retrying
    let retry_res = service
        .retry_dead_letter(Request::new(pb::RetryDeadLetterRequest {
            org_id: org1.to_string(),
            dead_letter_id: dl1_id_str.clone(),
        }))
        .await
        .expect("retry failed")
        .into_inner();

    let record = retry_res.record.expect("record missing in retry response");
    assert_eq!(record.state, pb::DeadLetterState::Retrying as i32);
    assert_eq!(record.retry_count, 1);

    // 5. Retry on non-failed record returns NotFound
    let retry_again_err = service
        .retry_dead_letter(Request::new(pb::RetryDeadLetterRequest {
            org_id: org1.to_string(),
            dead_letter_id: dl1_id_str.clone(),
        }))
        .await
        .unwrap_err();
    assert_eq!(retry_again_err.code(), Code::NotFound);

    // 6. Skip on non-failed record returns NotFound
    let skip_err = service
        .skip_dead_letter(Request::new(pb::SkipDeadLetterRequest {
            org_id: org1.to_string(),
            dead_letter_id: dl1_id_str.clone(),
        }))
        .await
        .unwrap_err();
    assert_eq!(skip_err.code(), Code::NotFound);

    // 7. Create another dead letter and test Skip transition
    let dl2 = store
        .create(CreateDeadLetterParams {
            org_id: org1,
            session_id,
            effect_id: EffectId::new(),
            undo_id: UndoId::new(),
            compensation_fn: "cancel_reservation".to_string(),
            compensation_version: "1.0.0".to_string(),
            compensation_args: serde_json::json!({ "seat": "4A" }),
            error_message: "permanent rejection".to_string(),
            retry_count: 0,
        })
        .await
        .expect("Failed to create second dead letter");

    let dl2_id_str = dl2.dead_letter_id.to_string();

    let skip_res = service
        .skip_dead_letter(Request::new(pb::SkipDeadLetterRequest {
            org_id: org1.to_string(),
            dead_letter_id: dl2_id_str.clone(),
        }))
        .await
        .expect("skip failed")
        .into_inner();

    let record2 = skip_res.record.expect("record missing in skip response");
    assert_eq!(record2.state, pb::DeadLetterState::Skipped as i32);

    // 8. Skip again returns NotFound
    let skip_again_err = service
        .skip_dead_letter(Request::new(pb::SkipDeadLetterRequest {
            org_id: org1.to_string(),
            dead_letter_id: dl2_id_str,
        }))
        .await
        .unwrap_err();
    assert_eq!(skip_again_err.code(), Code::NotFound);
}
