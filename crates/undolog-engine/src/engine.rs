//! EffectEngine - the core safety kernel for tool call interception.
//!
//! The engine implements deterministic, crash-safe tool call routing across three tiers:
//! - **Safe**: Read-only or idempotent calls; bypass the effect log entirely.
//! - **Compensable**: Write operations with defined compensation; logged with undo entries.
//! - **Irreversible**: Cannot be undone; require explicit human approval before execution.
//!
//! All operations are:
//! - **Deterministic**: Same inputs always produce the same outcome.
//! - **Idempotent**: Multiple calls with the same signature return `Replay` (no duplicates).
//! - **Crash-safe**: Advisory locks + exactly-once semantics prevent concurrent write races.

use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::{debug, info, instrument};

use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::{
    approval::{ApprovalAction, ApprovalRequest, ApprovalState},
    effect::{ToolCall, ToolResult},
    errors::UndoLogError,
    ids::{ApprovalRequestId, EffectId, OrgId},
    tier::ToolTier,
};

use crate::TierRegistry;

// ── Configuration ──────────────────────────────────────────────────────────

/// Engine configuration - advisory lock parameters and future extensibility.
#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// Maximum number of times to retry acquiring advisory lock (default: 3).
    pub lock_max_attempts: u32,
    /// Delay in milliseconds between lock acquisition attempts (default: 100).
    pub lock_retry_ms: u64,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self { lock_max_attempts: 3, lock_retry_ms: 100 }
    }
}

// ── Intercept Outcome ──────────────────────────────────────────────────────

/// What the Effect Engine instructs the Go MCP proxy to do next.
#[derive(Debug, Clone)]
pub enum InterceptOutcome {
    /// New call; safe to execute. Effect logged (or skipped for Safe tier).
    Execute { effect_id: EffectId },
    /// Duplicate detected (same call_signature). Return the cached result.
    Replay { effect_id: EffectId, result: Option<ToolResult> },
    /// Irreversible action; execution suspended pending human approval.
    AwaitingApproval { effect_id: EffectId, approval_request_id: ApprovalRequestId },
}

// ── EffectEngine ───────────────────────────────────────────────────────────

/// The core UndoLog Effect Engine - safety kernel for deterministic tool execution.
///
/// Handles tool call interception, logging, state transitions, and approval workflow.
#[derive(Clone)]
pub struct EffectEngine {
    effect_store: EffectStore,
    session_store: SessionStore,
    approval_store: ApprovalStore,
    registry: Arc<RwLock<TierRegistry>>,
    config: EngineConfig,
}

impl EffectEngine {
    /// Create a new `EffectEngine` with the given stores and config.
    pub fn new(
        effect_store: EffectStore,
        session_store: SessionStore,
        approval_store: ApprovalStore,
        registry: Arc<RwLock<TierRegistry>>,
        config: EngineConfig,
    ) -> Self {
        Self { effect_store, session_store, approval_store, registry, config }
    }

    /// Intercept a tool call before execution.
    ///
    /// This is the hot path - called for every tool invocation. It must be:
    /// - **Fast**: O(1) tier lookup via `TierRegistry`.
    /// - **Deterministic**: Same inputs always produce the same outcome.
    /// - **Idempotent**: Duplicate signatures return `Replay`, not duplicates.
    /// - **Crash-safe**: Advisory lock + exactly-once DB semantics prevent races.
    #[instrument(skip(self, call), fields(
        org_id = %call.org_id,
        session_id = %call.session_id,
        tool = %call.tool_name,
        step = call.step_index,
    ))]
    pub async fn intercept(&self, call: ToolCall) -> Result<InterceptOutcome, UndoLogError> {
        // Verify session exists before proceeding.
        let session = self.session_store.get_session(&call.org_id, &call.session_id).await?;
        if session.is_none() {
            return Err(UndoLogError::Internal(format!("Session not found: {}", call.session_id)));
        }

        let signature = call.signature();

        // Acquire advisory lock to prevent concurrent writes with the same signature.
        self.effect_store
            .acquire_advisory_lock(
                &signature,
                self.config.lock_max_attempts,
                self.config.lock_retry_ms,
            )
            .await?;

        // Check for replay: has this call already been intercepted?
        if let Some(existing) =
            self.effect_store.find_by_signature(&call.org_id, &signature).await?
        {
            // Increment replay counter.
            self.effect_store.mark_replayed(&call.org_id, &existing.effect_id).await?;

            debug!(
                effect_id = %existing.effect_id,
                replay_count = existing.replay_count + 1,
                "Replay detected - returning cached result"
            );

            return Ok(InterceptOutcome::Replay {
                effect_id: existing.effect_id,
                result: existing.result_snapshot,
            });
        }

        // Resolve the tool's tier from registry.
        let tier = self.resolve_tier(&call.org_id, &call.tool_name, &call.tool_version).await;

        // Generate a unique effect ID for this tool call.
        let effect_id = EffectId::new();

        debug!(
            effect_id = %effect_id,
            tier = %tier.label(),
            "Routing tool call to tier handler"
        );

        // Match on tier and execute tier-specific logic.
        match &tier {
            ToolTier::Safe => {
                // Safe tools bypass the effect log entirely - no entry created.
                debug!("Safe tier: bypassing effect log");
                Ok(InterceptOutcome::Execute { effect_id })
            }

            ToolTier::Compensable { compensation } => {
                // Insert into effect log with state = Pending.
                let inserted = self
                    .effect_store
                    .insert_compensable(&call, &signature, &effect_id, compensation)
                    .await?;

                if !inserted {
                    // ON CONFLICT fired; another writer beat us to it.
                    // This should not happen because we held the advisory lock, but handle gracefully.
                    if let Some(existing) =
                        self.effect_store.find_by_signature(&call.org_id, &signature).await?
                    {
                        return Ok(InterceptOutcome::Replay {
                            effect_id: existing.effect_id,
                            result: existing.result_snapshot,
                        });
                    }
                }

                // Push undo entry onto the stack (critical: must complete before execution).
                self.effect_store.push_undo_entry(&call, &effect_id, compensation).await?;

                info!(
                    effect_id = %effect_id,
                    fn_name = %compensation.fn_name,
                    "Compensable effect registered with undo entry"
                );

                Ok(InterceptOutcome::Execute { effect_id })
            }

            ToolTier::Irreversible { reason } => {
                // Insert into effect log with state = Pending.
                let inserted =
                    self.effect_store.insert_irreversible(&call, &signature, &effect_id).await?;

                if !inserted {
                    // Duplicate detected.
                    if let Some(existing) =
                        self.effect_store.find_by_signature(&call.org_id, &signature).await?
                    {
                        return Ok(InterceptOutcome::Replay {
                            effect_id: existing.effect_id,
                            result: existing.result_snapshot,
                        });
                    }
                }

                // Create approval request with context.
                let approval_request_id = ApprovalRequestId::new();
                let approval_req = ApprovalRequest {
                    approval_request_id,
                    org_id: call.org_id,
                    session_id: call.session_id,
                    effect_id,
                    tool_name: call.tool_name.clone(),
                    irreversibility_reason: reason.clone(),
                    risk_tags: vec![],
                    estimated_impact: None,
                    proposed_args: call.args.clone(),
                    agent_context: serde_json::json!({}),
                    state: ApprovalState::Pending,
                    timeout_at: chrono::Utc::now() + chrono::Duration::hours(24),
                    auto_approve_on_timeout: false,
                    resolved_at: None,
                    resolved_by: None,
                    approved_args: None,
                    created_at: chrono::Utc::now(),
                };

                // Persist approval request.
                self.approval_store.create(&approval_req).await?;

                // Link approval to effect.
                self.effect_store
                    .set_approval_request_id(&call.org_id, &effect_id, &approval_request_id)
                    .await?;

                // Suspend the session.
                self.session_store.set_awaiting_approval(&call.org_id, &call.session_id).await?;

                info!(
                    effect_id = %effect_id,
                    approval_request_id = %approval_request_id,
                    reason = %reason,
                    "Irreversible effect created; session awaiting approval"
                );

                Ok(InterceptOutcome::AwaitingApproval { effect_id, approval_request_id })
            }
        }
    }

    /// Mark an effect as executing (called just before invoking the tool).
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn set_executing(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
    ) -> Result<(), UndoLogError> {
        self.effect_store.set_executing(org_id, effect_id).await
    }

    /// Commit an effect (tool executed successfully; cache result).
    #[instrument(skip(self, result), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn commit(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        result: ToolResult,
    ) -> Result<(), UndoLogError> {
        self.effect_store.commit_effect(org_id, effect_id, result).await
    }

    /// Fail an effect (tool execution failed; record error and revert to Pending).
    #[instrument(skip(self), fields(org_id = %org_id, effect_id = %effect_id))]
    pub async fn fail(
        &self,
        org_id: &OrgId,
        effect_id: &EffectId,
        reason: &str,
    ) -> Result<(), UndoLogError> {
        self.effect_store.fail_effect(org_id, effect_id, reason).await
    }

    /// Approve an Irreversible action (human has granted permission).
    #[instrument(skip(self), fields(org_id = %org_id, approval_request_id = %approval_request_id, actor = %actor))]
    pub async fn approve(
        &self,
        org_id: &OrgId,
        approval_request_id: &ApprovalRequestId,
        actor: &str,
        approved_args: Option<serde_json::Value>,
    ) -> Result<(), UndoLogError> {
        // Resolve the approval request with Approve action.
        self.approval_store
            .resolve(
                org_id,
                approval_request_id,
                &ApprovalAction::Approve,
                actor,
                approved_args,
                None,
            )
            .await?;

        info!(approval_request_id = %approval_request_id, "Approval granted");

        Ok(())
    }

    /// Reject an Irreversible action (human has denied permission).
    #[instrument(skip(self), fields(org_id = %org_id, approval_request_id = %approval_request_id, actor = %actor))]
    pub async fn reject(
        &self,
        org_id: &OrgId,
        approval_request_id: &ApprovalRequestId,
        actor: &str,
    ) -> Result<(), UndoLogError> {
        // Resolve the approval request with Reject action.
        self.approval_store
            .resolve(org_id, approval_request_id, &ApprovalAction::Reject, actor, None, None)
            .await?;

        info!(approval_request_id = %approval_request_id, "Approval rejected");

        Ok(())
    }

    // ── Private helpers ────────────────────────────────────────────────────

    /// Resolve the tool's tier from the registry.
    ///
    /// Falls back to `Safe` if the tool is not registered.
    async fn resolve_tier(&self, org_id: &OrgId, tool_name: &str, tool_version: &str) -> ToolTier {
        let registry = self.registry.read().await;
        if let Some(registration) = registry.get(org_id, tool_name, tool_version).await {
            registration.tier
        } else {
            debug!(
                tool = %tool_name,
                version = %tool_version,
                "Tool not registered; defaulting to Safe tier"
            );
            ToolTier::Safe
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_config_defaults() {
        let config = EngineConfig::default();
        assert_eq!(config.lock_max_attempts, 3);
        assert_eq!(config.lock_retry_ms, 100);
    }
}
