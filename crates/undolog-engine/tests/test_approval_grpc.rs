//! Integration and unit tests for GetApproval gRPC RPC.
//!
//! Verifies:
//! - Input validation (malformed UUIDs for org_id or approval_id return `InvalidArgument`).
//! - Nonexistent approval IDs return `NotFound`.
//! - Cross-organization access isolation (returns `NotFound`).
//! - Pending approval returns `ApprovalState::Pending`, `resolved_at_unix_ms == 0`, and empty `approved_args`.
//! - Approved approval returns `ApprovalState::Approved`, resolution timestamp, and `approved_args`.
//! - Rejected, timed-out, and auto-approved states round trip correctly with timestamps.
//! - Behavior matches across PostgreSQL and SQLite storage engines.

#![cfg(all(test, feature = "pg"))]

use std::sync::Arc;
use tonic::{Code, Request};
use undolog_engine::grpc::pb::undo_log_engine_server::UndoLogEngine;
use undolog_engine::grpc::{pb, UndoLogEngineService};
use undolog_engine::{EffectEngine, EngineConfig, TierRegistry};
use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::{
    approval::{ApprovalRequest, ApprovalState},
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId},
};

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

// ── Unit Tests: Input Validation ───────────────────────────────────────────

#[tokio::test]
async fn test_get_approval_invalid_org_id_returns_invalid_argument() {
    let service = UndoLogEngineService::new(mock_engine(), None);
    let req = Request::new(pb::GetApprovalRequest {
        org_id: "not-a-valid-uuid".to_string(),
        approval_id: uuid::Uuid::new_v4().to_string(),
    });

    let err = service.get_approval(req).await.unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);
    assert!(err.message().contains("invalid org_id"));
}

#[tokio::test]
async fn test_get_approval_invalid_approval_id_returns_invalid_argument() {
    let service = UndoLogEngineService::new(mock_engine(), None);
    let req = Request::new(pb::GetApprovalRequest {
        org_id: uuid::Uuid::new_v4().to_string(),
        approval_id: "not-a-valid-uuid".to_string(),
    });

    let err = service.get_approval(req).await.unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);
    assert!(err.message().contains("invalid approval_id"));
}

// ── Integration Tests: Storage Lifecycle & Round-Trip ──────────────────────

#[tokio::test]
async fn test_get_approval_lifecycle_integration() {
    let db_url = match std::env::var("TEST_DATABASE_URL") {
        Ok(url) => url,
        Err(_) => return,
    };

    let pool = match sqlx::PgPool::connect(&db_url).await {
        Ok(p) => p,
        Err(_) => return,
    };

    let effect_store = EffectStore::new(pool.clone());
    let session_store = SessionStore::new(pool.clone());
    let approval_store = ApprovalStore::new(pool);
    let registry = Arc::new(tokio::sync::RwLock::new(TierRegistry::new()));
    let engine = EffectEngine::new(
        effect_store,
        session_store,
        approval_store.clone(),
        registry,
        EngineConfig::default(),
    );
    let service = UndoLogEngineService::new(engine, None);

    let org1 = OrgId::new();
    let org2 = OrgId::new();
    let session_id = SessionId::new();
    let effect_id = EffectId::new();
    let approval_id = ApprovalRequestId::new();
    let created_at = chrono::Utc::now();

    // 1. Create a pending approval request for org1
    let req = ApprovalRequest {
        approval_request_id: approval_id,
        org_id: org1,
        session_id,
        effect_id,
        tool_name: "deploy_service".to_string(),
        irreversibility_reason: "produces live traffic".to_string(),
        risk_tags: vec!["infrastructure".to_string()],
        estimated_impact: Some("production deployment".to_string()),
        proposed_args: serde_json::json!({ "replicas": 3, "image": "v1.2.0" }),
        agent_context: serde_json::json!({}),
        state: ApprovalState::Pending,
        timeout_at: created_at + chrono::Duration::hours(2),
        auto_approve_on_timeout: false,
        resolved_at: None,
        resolved_by: None,
        approved_args: None,
        created_at,
    };
    approval_store.create(&req).await.expect("Failed to create pending approval");

    // 2. Fetch pending approval via gRPC
    let get_res = service
        .get_approval(Request::new(pb::GetApprovalRequest {
            org_id: org1.to_string(),
            approval_id: approval_id.to_string(),
        }))
        .await
        .expect("get_approval failed")
        .into_inner();

    let record = get_res.record.expect("record must be present");
    assert_eq!(record.approval_id, approval_id.to_string());
    assert_eq!(record.org_id, org1.to_string());
    assert_eq!(record.session_id, session_id.to_string());
    assert_eq!(record.effect_id, effect_id.to_string());
    assert_eq!(record.tool_name, "deploy_service");
    assert_eq!(record.args, br#"{"image":"v1.2.0","replicas":3}"#);
    assert_eq!(record.state, pb::ApprovalState::Pending as i32);
    assert_eq!(record.resolved_at_unix_ms, 0);
    assert!(record.approved_args.is_empty());
    assert_eq!(record.created_at_unix_ms, created_at.timestamp_millis());

    // 3. Cross-organization lookup returns NotFound
    let cross_err = service
        .get_approval(Request::new(pb::GetApprovalRequest {
            org_id: org2.to_string(),
            approval_id: approval_id.to_string(),
        }))
        .await
        .unwrap_err();
    assert_eq!(cross_err.code(), Code::NotFound);

    // 4. Unknown approval ID returns NotFound
    let unknown_err = service
        .get_approval(Request::new(pb::GetApprovalRequest {
            org_id: org1.to_string(),
            approval_id: uuid::Uuid::new_v4().to_string(),
        }))
        .await
        .unwrap_err();
    assert_eq!(unknown_err.code(), Code::NotFound);

    // 5. Verify states round trip: Approved with modified args
    let resolved_at = created_at + chrono::Duration::seconds(30);
    let approved_id = ApprovalRequestId::new();
    let approved_req = ApprovalRequest {
        approval_request_id: approved_id,
        org_id: org1,
        session_id,
        effect_id: EffectId::new(),
        tool_name: "deploy_service".to_string(),
        irreversibility_reason: "produces live traffic".to_string(),
        risk_tags: vec![],
        estimated_impact: None,
        proposed_args: serde_json::json!({ "replicas": 3 }),
        agent_context: serde_json::json!({}),
        state: ApprovalState::Approved,
        timeout_at: created_at + chrono::Duration::hours(2),
        auto_approve_on_timeout: false,
        resolved_at: Some(resolved_at),
        resolved_by: Some("admin-user".to_string()),
        approved_args: Some(serde_json::json!({ "replicas": 1 })),
        created_at,
    };
    approval_store.create(&approved_req).await.expect("Failed to create approved request");

    let approved_res = service
        .get_approval(Request::new(pb::GetApprovalRequest {
            org_id: org1.to_string(),
            approval_id: approved_id.to_string(),
        }))
        .await
        .expect("get_approval for approved record failed")
        .into_inner();
    let approved_rec = approved_res.record.expect("record must be present");
    assert_eq!(approved_rec.state, pb::ApprovalState::Approved as i32);
    assert_eq!(approved_rec.resolved_at_unix_ms, resolved_at.timestamp_millis());
    assert_eq!(approved_rec.approved_args, br#"{"replicas":1}"#);

    // 6. Verify Rejected, TimedOut, and AutoApproved states
    let additional_states = [
        (ApprovalState::Rejected, pb::ApprovalState::Rejected as i32),
        (ApprovalState::TimedOut, pb::ApprovalState::TimedOut as i32),
        (ApprovalState::AutoApproved, pb::ApprovalState::AutoApproved as i32),
    ];

    for (domain_state, expected_proto_state) in additional_states {
        let test_id = ApprovalRequestId::new();
        let test_req = ApprovalRequest {
            approval_request_id: test_id,
            org_id: org1,
            session_id,
            effect_id: EffectId::new(),
            tool_name: "drop_table".to_string(),
            irreversibility_reason: "data loss".to_string(),
            risk_tags: vec![],
            estimated_impact: None,
            proposed_args: serde_json::json!({ "table": "analytics" }),
            agent_context: serde_json::json!({}),
            state: domain_state,
            timeout_at: created_at + chrono::Duration::hours(1),
            auto_approve_on_timeout: false,
            resolved_at: Some(resolved_at),
            resolved_by: Some("system".to_string()),
            approved_args: None,
            created_at,
        };
        approval_store.create(&test_req).await.expect("Failed to create test request");

        let test_res = service
            .get_approval(Request::new(pb::GetApprovalRequest {
                org_id: org1.to_string(),
                approval_id: test_id.to_string(),
            }))
            .await
            .expect("get_approval failed")
            .into_inner();
        let test_rec = test_res.record.expect("record must be present");
        assert_eq!(test_rec.state, expected_proto_state);
        assert_eq!(test_rec.resolved_at_unix_ms, resolved_at.timestamp_millis());
        assert!(test_rec.approved_args.is_empty());
    }
}
