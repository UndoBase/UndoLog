//! undolog-types
//!
//! All shared domain types for the UndoLog AI Agent Safe Execution Runtime.

#![allow(missing_docs)]
//! Zero async. Zero DB dependencies. Safe to use in every SDK.

pub mod approval;
pub mod effect;
pub mod errors;
pub mod ids;
pub mod saga;
pub mod session;
pub mod tier;

// ── Flat re-exports at crate root for ergonomic imports ──────────────────────
pub use approval::{ApprovalAction, ApprovalRequest, ApprovalState};
pub use effect::{
    canonical_json, CallSignature, EffectRecord, EffectState, InterceptOutcome, ToolCall,
    ToolResult,
};
pub use errors::{UndoLogError, UndoLogResult};
pub use ids::{
    ApprovalEventId, ApprovalRequestId, CompensationId, EffectId, OrgId, ProjectId, SessionId,
    SnapshotId, ToolId, UndoId,
};
pub use saga::{SagaStepState, UndoEntry};
pub use session::{SessionRecord, SessionState};
pub use tier::{CompensationDescriptor, ToolTier};
