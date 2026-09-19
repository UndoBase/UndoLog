//! HTTP admin API for dead-letter queue management.
//!
//! Provides REST endpoints for inspecting, retrying, and skipping
//! dead-lettered compensations. Intended for operational use by
//! dashboards and CLI tools.

use std::fmt;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use tracing::instrument;

use undolog_store::dead_letter::{DeadLetterState, DeadLetterStore};
use undolog_types::errors::UndoLogError;

/// Shared application state for admin API handlers.
#[derive(Clone)]
pub struct AdminState {
    dead_letter_store: DeadLetterStore,
}

impl fmt::Debug for AdminState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AdminState").finish_non_exhaustive()
    }
}

impl AdminState {
    /// Create a new admin state with the given dead-letter store.
    pub fn new(dead_letter_store: DeadLetterStore) -> Self {
        Self { dead_letter_store }
    }
}

/// JSON response for a list of dead-letter records.
#[derive(Debug, Serialize, Deserialize)]
pub struct DeadLetterListResponse {
    pub records: Vec<DeadLetterRecordJson>,
    pub count: usize,
}

/// JSON representation of a dead-letter record.
#[derive(Debug, Serialize, Deserialize)]
pub struct DeadLetterRecordJson {
    pub dead_letter_id: uuid::Uuid,
    pub org_id: uuid::Uuid,
    pub session_id: uuid::Uuid,
    pub effect_id: uuid::Uuid,
    pub undo_id: uuid::Uuid,
    pub compensation_fn: String,
    pub compensation_version: String,
    pub compensation_args: serde_json::Value,
    pub error_message: String,
    pub retry_count: i32,
    pub state: String,
    pub created_at: String,
    pub updated_at: String,
}

impl From<undolog_store::dead_letter::DeadLetterRecord> for DeadLetterRecordJson {
    fn from(r: undolog_store::dead_letter::DeadLetterRecord) -> Self {
        Self {
            dead_letter_id: r.dead_letter_id,
            org_id: *r.org_id.as_uuid(),
            session_id: *r.session_id.as_uuid(),
            effect_id: *r.effect_id.as_uuid(),
            undo_id: *r.undo_id.as_uuid(),
            compensation_fn: r.compensation_fn,
            compensation_version: r.compensation_version,
            compensation_args: r.compensation_args,
            error_message: r.error_message,
            retry_count: r.retry_count,
            state: r.state.as_str().to_string(),
            created_at: r.created_at.to_rfc3339(),
            updated_at: r.updated_at.to_rfc3339(),
        }
    }
}

/// Query parameters for listing dead letters.
#[derive(Debug, Deserialize)]
pub struct ListDeadLettersParams {
    /// Organization ID to filter by.
    pub org_id: uuid::Uuid,
    /// State filter (defaults to `failed`).
    pub state: Option<String>,
}

/// Build the admin API router with the given shared state.
pub fn router(state: AdminState) -> Router {
    Router::new()
        .route("/admin/dead-letters", get(list_dead_letters))
        .route("/admin/dead-letters/{id}/retry", post(retry_dead_letter))
        .route("/admin/dead-letters/{id}/skip", post(skip_dead_letter))
        .with_state(state)
}

/// GET /admin/dead-letters?state=failed
///
/// List dead-letter records filtered by state. Defaults to `failed` if
/// no state parameter is provided.
#[instrument(skip(state))]
async fn list_dead_letters(
    State(state): State<AdminState>,
    Query(params): Query<ListDeadLettersParams>,
) -> Result<Json<DeadLetterListResponse>, (StatusCode, String)> {
    let state_filter = params
        .state
        .as_deref()
        .and_then(DeadLetterState::parse_str)
        .unwrap_or(DeadLetterState::Failed);

    let org_id: undolog_types::ids::OrgId = params.org_id.into();

    let records = state
        .dead_letter_store
        .list_by_state(org_id, state_filter)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("query failed: {e}")))?;

    let count = records.len();
    let response = DeadLetterListResponse {
        records: records.into_iter().map(DeadLetterRecordJson::from).collect(),
        count,
    };

    Ok(Json(response))
}

/// POST /admin/dead-letters/{id}/retry
///
/// Retry a dead-letter record by resetting its state to `retrying`.
/// Returns the updated record.
#[instrument(skip(state))]
async fn retry_dead_letter(
    State(state): State<AdminState>,
    Path(id): Path<uuid::Uuid>,
) -> Result<Json<DeadLetterRecordJson>, (StatusCode, String)> {
    let record = state.dead_letter_store.retry(id).await.map_err(|e| match e {
        UndoLogError::Database(ref db_err) => {
            if let sqlx::Error::RowNotFound = db_err {
                (StatusCode::NOT_FOUND, format!("dead letter {id} not found"))
            } else {
                (StatusCode::INTERNAL_SERVER_ERROR, format!("retry failed: {e}"))
            }
        }
        _ => (StatusCode::INTERNAL_SERVER_ERROR, format!("retry failed: {e}")),
    })?;

    Ok(Json(DeadLetterRecordJson::from(record)))
}

/// POST /admin/dead-letters/{id}/skip
///
/// Skip a dead-letter record by marking it as `skipped`.
/// Returns the updated record.
#[instrument(skip(state))]
async fn skip_dead_letter(
    State(state): State<AdminState>,
    Path(id): Path<uuid::Uuid>,
) -> Result<Json<DeadLetterRecordJson>, (StatusCode, String)> {
    let record = state.dead_letter_store.skip(id).await.map_err(|e| match e {
        UndoLogError::Database(ref db_err) => {
            if let sqlx::Error::RowNotFound = db_err {
                (StatusCode::NOT_FOUND, format!("dead letter {id} not found"))
            } else {
                (StatusCode::INTERNAL_SERVER_ERROR, format!("skip failed: {e}"))
            }
        }
        _ => (StatusCode::INTERNAL_SERVER_ERROR, format!("skip failed: {e}")),
    })?;

    Ok(Json(DeadLetterRecordJson::from(record)))
}

// ── Unit tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_admin_state_creation() {
        let pool = sqlx::PgPool::connect_lazy("postgresql://localhost/test").unwrap();
        let store = DeadLetterStore::new(pool);
        let state = AdminState::new(store);
        // Verify state was created successfully.
        let _ = state;
    }

    #[test]
    fn test_dead_letter_list_response_serialization() {
        let response = DeadLetterListResponse { records: vec![], count: 0 };
        let json = serde_json::to_value(&response).unwrap();
        assert_eq!(json["count"], 0);
        assert!(json["records"].as_array().unwrap().is_empty());
    }

    #[test]
    fn test_dead_letter_record_json_serialization() {
        let record = DeadLetterRecordJson {
            dead_letter_id: uuid::Uuid::new_v4(),
            org_id: uuid::Uuid::new_v4(),
            session_id: uuid::Uuid::new_v4(),
            effect_id: uuid::Uuid::new_v4(),
            undo_id: uuid::Uuid::new_v4(),
            compensation_fn: "cancel_payment".to_string(),
            compensation_version: "1.0.0".to_string(),
            compensation_args: serde_json::json!({"amount": 100}),
            error_message: "timeout".to_string(),
            retry_count: 3,
            state: "failed".to_string(),
            created_at: "2026-01-01T00:00:00Z".to_string(),
            updated_at: "2026-01-01T00:00:00Z".to_string(),
        };
        let json = serde_json::to_value(&record).unwrap();
        assert_eq!(json["state"], "failed");
        assert_eq!(json["retry_count"], 3);
        assert_eq!(json["compensation_fn"], "cancel_payment");
    }

    #[test]
    fn test_list_dead_letters_params_defaults() {
        let params = ListDeadLettersParams { org_id: uuid::Uuid::new_v4(), state: None };
        assert!(params.state.is_none());
    }

    #[test]
    fn test_list_dead_letters_params_with_state() {
        let params = ListDeadLettersParams {
            org_id: uuid::Uuid::new_v4(),
            state: Some("failed".to_string()),
        };
        assert_eq!(params.state.as_deref(), Some("failed"));
    }

    #[test]
    fn test_dead_letter_record_json_from_domain() {
        use undolog_store::dead_letter::DeadLetterRecord;
        use undolog_types::ids::{EffectId, OrgId, SessionId, UndoId};

        let record = DeadLetterRecord {
            dead_letter_id: uuid::Uuid::new_v4(),
            org_id: OrgId::new(),
            session_id: SessionId::new(),
            effect_id: EffectId::new(),
            undo_id: UndoId::new(),
            compensation_fn: "refund_payment".to_string(),
            compensation_version: "2.0.0".to_string(),
            compensation_args: serde_json::json!({"order_id": "123"}),
            error_message: "insufficient funds".to_string(),
            retry_count: 2,
            state: DeadLetterState::Failed,
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        };

        let json_record = DeadLetterRecordJson::from(record);
        assert_eq!(json_record.compensation_fn, "refund_payment");
        assert_eq!(json_record.state, "failed");
        assert_eq!(json_record.retry_count, 2);
    }
}
