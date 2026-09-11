#![cfg(feature = "sqlite")]

use undolog_store::sqlite::build_stores;
use undolog_types::{
    effect::{ToolCall, ToolResult},
    ids::{EffectId, OrgId, SessionId},
    session::SessionState,
    tier::{CompensationDescriptor, ToolTier},
};

fn make_call(org_id: OrgId, session_id: SessionId, step: u32) -> ToolCall {
    use chrono::Utc;
    ToolCall {
        org_id,
        session_id,
        tool_id: None,
        tool_name: "test_tool".to_string(),
        tool_version: "1.0.0".to_string(),
        tier: ToolTier::Safe,
        step_index: step,
        args: serde_json::json!({"key": "value"}),
        intercepted_at: Utc::now(),
    }
}

#[tokio::test]
async fn test_schema_init_and_session() {
    let (_, session_store, _) = build_stores(":memory:").await.unwrap();

    let org_id = OrgId::new();
    let session_id = SessionId::new();

    session_store.create_session(&org_id, &session_id).await.unwrap();

    let session = session_store.get_session(&org_id, &session_id).await.unwrap();
    assert!(session.is_some());
    let session = session.unwrap();
    assert_eq!(session.org_id, org_id);
    assert_eq!(session.session_id, session_id);
    assert_eq!(session.state, SessionState::Active);
}

#[tokio::test]
async fn test_effect_insert_and_find() {
    let (effect_store, _, _) = build_stores(":memory:").await.unwrap();

    let org_id = OrgId::new();
    let session_id = SessionId::new();
    let effect_id = EffectId::new();
    let call = make_call(org_id, session_id, 0);
    let signature = call.signature();
    let compensation = CompensationDescriptor::new("comp_test", serde_json::json!({}));

    let inserted = effect_store
        .insert_compensable(&call, &signature, &effect_id, &compensation)
        .await
        .unwrap();
    assert!(inserted);

    let found = effect_store.find_by_signature(&org_id, &signature).await.unwrap();
    assert!(found.is_some());
    let found = found.unwrap();
    assert_eq!(found.effect_id, effect_id);
    assert_eq!(found.tool_name, "test_tool");
}

#[tokio::test]
async fn test_session_state_transitions() {
    let (_, session_store, _) = build_stores(":memory:").await.unwrap();

    let org_id = OrgId::new();
    let session_id = SessionId::new();
    session_store.create_session(&org_id, &session_id).await.unwrap();

    session_store.set_compensating(&org_id, &session_id).await.unwrap();
    let session = session_store.get_session(&org_id, &session_id).await.unwrap().unwrap();
    assert_eq!(session.state, SessionState::Compensating);

    session_store.set_compensated(&org_id, &session_id).await.unwrap();
    let session = session_store.get_session(&org_id, &session_id).await.unwrap().unwrap();
    assert_eq!(session.state, SessionState::Compensated);
}

#[tokio::test]
async fn test_effect_commit() {
    let (effect_store, _, _) = build_stores(":memory:").await.unwrap();

    let org_id = OrgId::new();
    let session_id = SessionId::new();
    let effect_id = EffectId::new();
    let call = make_call(org_id, session_id, 0);
    let signature = call.signature();
    let compensation = CompensationDescriptor::new("comp_test", serde_json::json!({}));

    effect_store.insert_compensable(&call, &signature, &effect_id, &compensation).await.unwrap();

    effect_store.set_executing(&org_id, &effect_id).await.unwrap();

    let result = ToolResult {
        success: true,
        output: serde_json::json!({"ok": true}),
        error: None,
        duration_ms: 100,
    };
    effect_store.commit_effect(&org_id, &effect_id, result).await.unwrap();

    let found = effect_store.find_by_signature(&org_id, &signature).await.unwrap().unwrap();
    assert_eq!(found.state, undolog_types::effect::EffectState::Committed);
}

#[tokio::test]
async fn test_approval_create_and_get() {
    let (_, _, approval_store) = build_stores(":memory:").await.unwrap();

    let org_id = OrgId::new();
    let session_id = SessionId::new();
    let effect_id = EffectId::new();
    let approval_id = undolog_types::ids::ApprovalRequestId::new();

    let req = undolog_types::approval::ApprovalRequest {
        approval_request_id: approval_id,
        org_id,
        session_id,
        effect_id,
        tool_name: "dangerous_tool".to_string(),
        irreversibility_reason: "This cannot be undone".to_string(),
        risk_tags: vec!["data-loss".to_string()],
        estimated_impact: Some("High".to_string()),
        proposed_args: serde_json::json!({"target": "/data"}),
        agent_context: serde_json::json!({}),
        state: undolog_types::approval::ApprovalState::Pending,
        timeout_at: chrono::Utc::now() + chrono::Duration::hours(24),
        auto_approve_on_timeout: false,
        resolved_at: None,
        resolved_by: None,
        approved_args: None,
        created_at: chrono::Utc::now(),
    };

    approval_store.create(&req).await.unwrap();

    let loaded = approval_store.get(&org_id, &approval_id).await.unwrap();
    assert!(loaded.is_some());
    let loaded = loaded.unwrap();
    assert_eq!(loaded.tool_name, "dangerous_tool");
    assert_eq!(loaded.state, undolog_types::approval::ApprovalState::Pending);

    let pending = approval_store.list_pending(&org_id).await.unwrap();
    assert_eq!(pending.len(), 1);
}
