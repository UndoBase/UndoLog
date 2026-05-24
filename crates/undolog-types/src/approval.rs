//! Human-in-the-loop approval types.

use crate::ids::{ApprovalRequestId, EffectId, OrgId, SessionId};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Lifecycle state of a human-in-the-loop approval request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalState {
    /// Awaiting human decision.
    Pending,
    /// Human approved the irreversible action.
    Approved,
    /// Human rejected the irreversible action.
    Rejected,
    /// Approval window expired before a decision was made.
    TimedOut,
    /// Approved by policy (auto-approve on timeout).
    AutoApproved,
}

impl ApprovalState {
    /// Returns `true` if the approval reached a final state.
    pub fn is_terminal(&self) -> bool {
        !matches!(self, ApprovalState::Pending)
    }
}

/// Action taken by a human (or system) on an approval request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalAction {
    /// Approved as-is.
    Approve,
    /// Rejected; execution halted.
    Reject,
    /// Approver modified args before approving.
    Modify,
    /// Approval timed out without a decision.
    Timeout,
}

/// A pending or resolved human approval request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub approval_request_id: ApprovalRequestId,
    pub org_id: OrgId,
    pub session_id: SessionId,
    pub effect_id: EffectId,
    pub tool_name: String,
    pub irreversibility_reason: String,
    pub risk_tags: Vec<String>,
    pub estimated_impact: Option<String>,
    pub proposed_args: serde_json::Value,
    /// Last N effects from the session - shown as context in the approval UI.
    pub agent_context: serde_json::Value,
    pub state: ApprovalState,
    pub timeout_at: DateTime<Utc>,
    pub auto_approve_on_timeout: bool,
    pub resolved_at: Option<DateTime<Utc>>,
    pub resolved_by: Option<String>,
    /// Final args used for execution (may differ from proposed if approver modified).
    pub approved_args: Option<serde_json::Value>,
    pub created_at: DateTime<Utc>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pending_is_not_terminal() {
        assert!(!ApprovalState::Pending.is_terminal());
    }

    #[test]
    fn resolved_states_are_terminal() {
        for s in [
            ApprovalState::Approved,
            ApprovalState::Rejected,
            ApprovalState::TimedOut,
            ApprovalState::AutoApproved,
        ] {
            assert!(s.is_terminal(), "{s:?} should be terminal");
        }
    }
}
