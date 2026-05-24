//! Saga orchestration types - the undo stack.

use crate::{
    ids::{EffectId, OrgId, SessionId, UndoId},
    tier::CompensationDescriptor,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Lifecycle state of a single undo stack entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SagaStepState {
    Pending,
    Running,
    Compensated,
    Failed,
    Skipped,
}

impl SagaStepState {
    /// Returns `true` if the step reached a final state.
    pub fn is_terminal(&self) -> bool {
        matches!(self, SagaStepState::Compensated | SagaStepState::Failed | SagaStepState::Skipped)
    }
}

/// One entry in the per-session undo stack.
///
/// # Key invariant
/// `registered_at` MUST precede the action's execution timestamp.
/// The Saga Orchestrator enforces this by pushing the entry before calling
/// the tool. A process crash after push but before execution is safe -
/// the compensation is already persisted and will be re-attempted on recovery.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UndoEntry {
    /// Unique undo stack entry identifier.
    pub undo_id: UndoId,
    /// Tenant organisation.
    pub org_id: OrgId,
    /// Session this entry belongs to.
    pub session_id: SessionId,
    /// The original effect being compensated.
    pub effect_id: EffectId,
    /// LIFO ordering: higher value = pushed later = compensated first.
    pub stack_position: u32,
    /// Compensation function descriptor captured before execution.
    pub compensation: CompensationDescriptor,
    /// Current lifecycle state of this undo entry.
    pub state: SagaStepState,
    /// Number of retry attempts so far.
    pub retry_count: u8,
    /// Most recent error message from compensation execution.
    pub last_error: Option<String>,
    /// Set BEFORE the action executes (the defining safety invariant).
    pub registered_at: DateTime<Utc>,
    /// When compensation completed.
    pub compensated_at: Option<DateTime<Utc>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_states() {
        assert!(SagaStepState::Compensated.is_terminal());
        assert!(SagaStepState::Failed.is_terminal());
        assert!(SagaStepState::Skipped.is_terminal());
        assert!(!SagaStepState::Pending.is_terminal());
        assert!(!SagaStepState::Running.is_terminal());
    }
}
