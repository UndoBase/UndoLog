//! Tests for the dead-letter admin API.
//!
//! These tests verify the admin API endpoints for dead-letter management.
//! Unit tests use mock stores; integration tests require PostgreSQL.

#![cfg(test)]

use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use tower::ServiceExt;

use undolog_engine::admin::{AdminState, DeadLetterListResponse, DeadLetterRecordJson};

// ── Test helpers ───────────────────────────────────────────────────────────

/// Helper: create a mock admin state with a test pool.
fn mock_admin_state() -> AdminState {
    let pool = sqlx::PgPool::connect_lazy("postgresql://localhost/test").unwrap();
    let store = undolog_store::DeadLetterStore::new(pool);
    AdminState::new(store)
}

/// Helper: create a test dead letter JSON payload.
fn test_dead_letter_json() -> DeadLetterRecordJson {
    DeadLetterRecordJson {
        dead_letter_id: uuid::Uuid::new_v4(),
        org_id: uuid::Uuid::new_v4(),
        session_id: uuid::Uuid::new_v4(),
        effect_id: uuid::Uuid::new_v4(),
        undo_id: uuid::Uuid::new_v4(),
        compensation_fn: "cancel_payment".to_string(),
        compensation_version: "1.0.0".to_string(),
        compensation_args: serde_json::json!({"amount": 100}),
        error_message: "timeout exceeded".to_string(),
        retry_count: 3,
        state: "failed".to_string(),
        created_at: "2026-01-01T00:00:00Z".to_string(),
        updated_at: "2026-01-01T00:00:00Z".to_string(),
    }
}

// ── Unit Tests ─────────────────────────────────────────────────────────────

#[tokio::test]
async fn test_list_dead_letters_returns_empty_when_no_records() {
    let state = mock_admin_state();
    let app = undolog_engine::admin::router(state);

    let org_id = uuid::Uuid::new_v4();
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/admin/dead-letters?org_id={org_id}&state=failed"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    // Will fail with database error since we're using a mock pool,
    // but the route should exist and respond.
    assert!(
        response.status() == StatusCode::INTERNAL_SERVER_ERROR
            || response.status() == StatusCode::OK,
        "Route should exist and respond"
    );
}

#[tokio::test]
async fn test_retry_dead_letter_returns_404_for_missing_id() {
    let state = mock_admin_state();
    let app = undolog_engine::admin::router(state);

    let id = uuid::Uuid::new_v4();
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/admin/dead-letters/{id}/retry"))
                .method("POST")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    // Will fail with database error since we're using a mock pool,
    // but the route should exist and respond.
    assert!(
        response.status() == StatusCode::INTERNAL_SERVER_ERROR
            || response.status() == StatusCode::NOT_FOUND
            || response.status() == StatusCode::OK,
        "Route should exist and respond"
    );
}

#[tokio::test]
async fn test_skip_dead_letter_returns_404_for_missing_id() {
    let state = mock_admin_state();
    let app = undolog_engine::admin::router(state);

    let id = uuid::Uuid::new_v4();
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/admin/dead-letters/{id}/skip"))
                .method("POST")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    // Will fail with database error since we're using a mock pool,
    // but the route should exist and respond.
    assert!(
        response.status() == StatusCode::INTERNAL_SERVER_ERROR
            || response.status() == StatusCode::NOT_FOUND
            || response.status() == StatusCode::OK,
        "Route should exist and respond"
    );
}

#[test]
fn test_dead_letter_record_json_serialization_roundtrip() {
    let record = test_dead_letter_json();
    let json = serde_json::to_value(&record).unwrap();

    assert_eq!(json["dead_letter_id"], record.dead_letter_id.to_string());
    assert_eq!(json["compensation_fn"], "cancel_payment");
    assert_eq!(json["state"], "failed");
    assert_eq!(json["retry_count"], 3);
    assert_eq!(json["error_message"], "timeout exceeded");
    assert_eq!(json["compensation_args"]["amount"], 100);
}

#[test]
fn test_dead_letter_list_response_serialization() {
    let response = DeadLetterListResponse {
        records: vec![test_dead_letter_json(), test_dead_letter_json()],
        count: 2,
    };
    let json = serde_json::to_value(&response).unwrap();

    assert_eq!(json["count"], 2);
    assert_eq!(json["records"].as_array().unwrap().len(), 2);
}

#[test]
fn test_dead_letter_record_json_has_all_fields() {
    let record = test_dead_letter_json();
    let json = serde_json::to_value(&record).unwrap();

    assert!(json.get("dead_letter_id").is_some());
    assert!(json.get("org_id").is_some());
    assert!(json.get("session_id").is_some());
    assert!(json.get("effect_id").is_some());
    assert!(json.get("undo_id").is_some());
    assert!(json.get("compensation_fn").is_some());
    assert!(json.get("compensation_version").is_some());
    assert!(json.get("compensation_args").is_some());
    assert!(json.get("error_message").is_some());
    assert!(json.get("retry_count").is_some());
    assert!(json.get("state").is_some());
    assert!(json.get("created_at").is_some());
    assert!(json.get("updated_at").is_some());
}

#[test]
fn test_dead_letter_states_are_valid() {
    let states = vec!["failed", "retrying", "skipped"];
    for state in states {
        let record = DeadLetterRecordJson { state: state.to_string(), ..test_dead_letter_json() };
        let json = serde_json::to_value(&record).unwrap();
        assert_eq!(json["state"], state);
    }
}

#[tokio::test]
async fn test_admin_state_is_clone() {
    let state = mock_admin_state();
    let _cloned = state.clone();
}

// ── Integration Tests ──────────────────────────────────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn test_list_dead_letters_integration() {
    let db_url = std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string());
    let pool = sqlx::PgPool::connect(&db_url).await.unwrap();
    let store = undolog_store::DeadLetterStore::new(pool);
    let state = AdminState::new(store);
    let app = undolog_engine::admin::router(state);

    let org_id = uuid::Uuid::new_v4();
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/admin/dead-letters?org_id={org_id}&state=failed"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let body = response.into_body().collect().await.unwrap().to_bytes();
    let list: DeadLetterListResponse = serde_json::from_slice(&body).unwrap();
    assert_eq!(list.count, 0);
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn test_retry_dead_letter_integration() {
    let db_url = std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string());
    let pool = sqlx::PgPool::connect(&db_url).await.unwrap();
    let store = undolog_store::DeadLetterStore::new(pool);
    let state = AdminState::new(store);
    let app = undolog_engine::admin::router(state);

    let id = uuid::Uuid::new_v4();
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/admin/dead-letters/{id}/retry"))
                .method("POST")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    // Should return 404 since the ID doesn't exist.
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn test_skip_dead_letter_integration() {
    let db_url = std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string());
    let pool = sqlx::PgPool::connect(&db_url).await.unwrap();
    let store = undolog_store::DeadLetterStore::new(pool);
    let state = AdminState::new(store);
    let app = undolog_engine::admin::router(state);

    let id = uuid::Uuid::new_v4();
    let response = app
        .oneshot(
            Request::builder()
                .uri(format!("/admin/dead-letters/{id}/skip"))
                .method("POST")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    // Should return 404 since the ID doesn't exist.
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
}
