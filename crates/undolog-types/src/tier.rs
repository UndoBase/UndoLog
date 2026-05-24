//! Tool tier classification - the core UndoLog contract.
//!
//! Every tool is classified at registration time into exactly one tier.
//! Classification is declarative (SDK annotation), never inferred by the LLM.

use serde::{Deserialize, Serialize};

/// How the Effect Engine treats an intercepted tool call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "tier", rename_all = "snake_case")]
pub enum ToolTier {
    /// Read-only or idempotent. Execute freely; no effect log entry required.
    ///
    /// Examples: `search_web`, `read_file`, `get_user`
    Safe,

    /// Write operation with a well-defined compensation (undo).
    ///
    /// Examples: `send_email`, `transfer_funds`, `create_record`
    ///
    /// UndoLog: compensation is registered in the undo stack **before** execution.
    Compensable { compensation: CompensationDescriptor },

    /// Cannot be undone. Requires explicit human approval before execution.
    ///
    /// Examples: `delete_database`, `publish_to_production`, `wire_large_amount`
    Irreversible {
        /// Human-readable explanation shown in the approval UI. Must be non-empty.
        reason: String,
    },
}

impl ToolTier {
    pub fn is_safe(&self) -> bool {
        matches!(self, ToolTier::Safe)
    }

    pub fn is_compensable(&self) -> bool {
        matches!(self, ToolTier::Compensable { .. })
    }

    pub fn requires_approval(&self) -> bool {
        matches!(self, ToolTier::Irreversible { .. })
    }

    pub fn compensation(&self) -> Option<&CompensationDescriptor> {
        match self {
            ToolTier::Compensable { compensation } => Some(compensation),
            _ => None,
        }
    }

    pub fn irreversibility_reason(&self) -> Option<&str> {
        match self {
            ToolTier::Irreversible { reason } => Some(reason.as_str()),
            _ => None,
        }
    }

    /// Short lowercase label - used in DB enum columns and log fields.
    pub fn label(&self) -> &'static str {
        match self {
            ToolTier::Safe => "safe",
            ToolTier::Compensable { .. } => "compensable",
            ToolTier::Irreversible { .. } => "irreversible",
        }
    }
}

/// Describes the compensation function to invoke when rolling back a
/// `Compensable` tool call.
///
/// Stored in the undo stack entry **before** the action executes, so that
/// a process crash cannot lose the compensation information.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompensationDescriptor {
    /// Logical name matching `undolog_compensation_registry.fn_name`.
    pub fn_name: String,
    /// Semver version of the compensation function.
    pub fn_version: String,
    /// Arguments captured from the original call **before** execution.
    pub args: serde_json::Value,
    /// Max retry attempts before escalating to `compensation_failed`.
    pub max_retries: u8,
    /// Backoff delay between retries in milliseconds.
    pub retry_backoff_ms: u32,
}

impl CompensationDescriptor {
    /// Build a descriptor with sensible defaults for version, retries, and backoff.
    pub fn new(fn_name: impl Into<String>, args: serde_json::Value) -> Self {
        Self {
            fn_name: fn_name.into(),
            fn_version: "1.0.0".to_string(),
            args,
            max_retries: 3,
            retry_backoff_ms: 1_000,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn safe_flags() {
        let t = ToolTier::Safe;
        assert!(t.is_safe());
        assert!(!t.is_compensable());
        assert!(!t.requires_approval());
        assert_eq!(t.label(), "safe");
    }

    #[test]
    fn compensable_flags() {
        let t = ToolTier::Compensable {
            compensation: CompensationDescriptor::new("cancel_payment", json!({})),
        };
        assert!(!t.is_safe());
        assert!(t.is_compensable());
        assert!(!t.requires_approval());
        assert_eq!(t.label(), "compensable");
        assert!(t.compensation().is_some());
    }

    #[test]
    fn irreversible_flags() {
        let t = ToolTier::Irreversible { reason: "Permanently deletes all data.".to_string() };
        assert!(!t.is_safe());
        assert!(!t.is_compensable());
        assert!(t.requires_approval());
        assert_eq!(t.label(), "irreversible");
        assert_eq!(t.irreversibility_reason(), Some("Permanently deletes all data."));
    }

    #[test]
    fn json_roundtrip() {
        let tier = ToolTier::Compensable {
            compensation: CompensationDescriptor::new("undo_create", json!({"path": "/tmp/x"})),
        };
        let json = serde_json::to_string(&tier).unwrap();
        let decoded: ToolTier = serde_json::from_str(&json).unwrap();
        assert_eq!(tier, decoded);
    }
}
