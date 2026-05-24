//! Session domain types.

use crate::ids::{OrgId, ProjectId, SessionId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Lifecycle state of an agent execution session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionState {
    Active,
    Completed,
    Failed,
    /// Walking the undo stack to roll back compensable steps.
    Compensating,
    /// All compensations completed.
    Compensated,
    /// Waiting for a human to approve an Irreversible action.
    AwaitingApproval,
    /// A compensation failed permanently; manual intervention required.
    Halted,
}

impl SessionState {
    /// Returns `true` if the session reached a final state.
    pub fn is_terminal(&self) -> bool {
        matches!(self, SessionState::Completed | SessionState::Compensated | SessionState::Halted)
    }
}

impl std::fmt::Display for SessionState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = serde_json::to_value(self)
            .ok()
            .and_then(|v| v.as_str().map(str::to_owned))
            .unwrap_or_default();
        write!(f, "{}", s)
    }
}

/// Full session record matching a row in `undolog_sessions`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionRecord {
    /// Unique session identifier.
    pub session_id: SessionId,
    /// Tenant organisation.
    pub org_id: OrgId,
    /// Optional project grouping.
    pub project_id: Option<ProjectId>,
    /// External run identifier from the orchestration framework.
    pub external_run_id: Option<String>,
    /// Human-readable agent name.
    pub agent_name: Option<String>,
    /// Current lifecycle state.
    pub state: SessionState,
    /// Total tool calls made in this session.
    pub tool_calls_total: u32,
    /// Total compensations executed.
    pub compensations_total: u32,
    /// Number of approvals still pending.
    pub approvals_pending: u32,
    /// Session start timestamp.
    pub started_at: DateTime<Utc>,
    /// Session completion timestamp.
    pub completed_at: Option<DateTime<Utc>>,
    /// Session failure timestamp.
    pub failed_at: Option<DateTime<Utc>>,
    /// Human-readable failure reason.
    pub failure_reason: Option<String>,
    /// Flexible metadata blob from the orchestration framework.
    pub metadata: serde_json::Value,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_states() {
        assert!(SessionState::Completed.is_terminal());
        assert!(SessionState::Compensated.is_terminal());
        assert!(SessionState::Halted.is_terminal());
        assert!(!SessionState::Active.is_terminal());
        assert!(!SessionState::AwaitingApproval.is_terminal());
    }
}
