//! Error types for the UndoLog runtime.

use thiserror::Error;

/// Errors produced by the UndoLog runtime across all crates.
#[derive(Debug, Error)]
pub enum UndoLogError {
    /// Tool was not found in the registry.
    #[error("Tool '{tool_name}' is not registered for org '{org_id}'")]
    ToolNotRegistered { tool_name: String, org_id: String },

    #[error("Call signature conflict: effect with signature '{signature}' already exists")]
    DuplicateSignature { signature: String },

    #[error(
        "Effect '{effect_id}' is in state '{current_state}', cannot transition to '{target_state}'"
    )]
    InvalidStateTransition { effect_id: String, current_state: String, target_state: String },

    #[error("Cannot commit effect '{effect_id}': not in 'executing' state")]
    NotExecuting { effect_id: String },

    // ── Saga ──────────────────────────────────────────────────────────────
    #[error("Compensation '{fn_name}' failed after {retries} retries: {reason}")]
    CompensationFailed { fn_name: String, retries: u8, reason: String },

    #[error("Undo stack for session '{session_id}' is empty")]
    EmptyUndoStack { session_id: String },

    #[error("Compensation registered after action executed for effect '{effect_id}' - invariant violated")]
    CompensationRegisteredTooLate { effect_id: String },

    // ── Approval ──────────────────────────────────────────────────────────
    #[error("Approval request '{approval_id}' has already been resolved")]
    ApprovalAlreadyResolved { approval_id: String },

    #[error("Approval request '{approval_id}' timed out")]
    ApprovalTimedOut { approval_id: String },

    #[error("Approval request '{approval_id}' not found")]
    ApprovalNotFound { approval_id: String },

    // ── Advisory lock ─────────────────────────────────────────────────────
    #[error(
        "Could not acquire advisory lock for signature '{signature}' after {attempts} attempts"
    )]
    AdvisoryLockTimeout { signature: String, attempts: u32 },

    // ── Storage ───────────────────────────────────────────────────────────
    #[error("Database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    // ── Generic ───────────────────────────────────────────────────────────
    #[error("Internal error: {0}")]
    Internal(String),
}

/// Convenience alias used throughout every UndoLog crate.
pub type UndoLogResult<T> = Result<T, UndoLogError>;
