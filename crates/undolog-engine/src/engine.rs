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
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId},
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

/// Data returned by approve() for the proxy to execute the tool.
#[derive(Debug, Clone)]
pub struct ApprovalResult {
    /// The approved effect to execute.
    pub effect_id: EffectId,
    /// The session the effect belongs to.
    pub session_id: SessionId,
    /// Name of the tool to execute.
    pub tool_name: String,
    /// Version of the tool from the effect record.
    pub tool_version: String,
    /// Resolved args (approved_args if provided, otherwise proposed_args).
    pub args: serde_json::Value,
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
        // Auto-create session on first use if it doesn't exist.
        let existing = self.session_store.get_session(&call.org_id, &call.session_id).await?;
        if existing.is_none() {
            self.session_store.create_session(&call.org_id, &call.session_id).await?;
            info!(
                org_id = %call.org_id,
                session_id = %call.session_id,
                "Auto-created session on first intercept"
            );
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
        let found = self.effect_store.find_by_signature(&call.org_id, &signature).await?;
        if let Some(existing) = found {
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

                // Transition from pending → executing so the proxy can commit.
                self.effect_store.set_executing(&call.org_id, &effect_id).await?;

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
                let now = chrono::Utc::now();
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
                    created_at: now,
                    timeout_at: now + chrono::Duration::hours(24),
                    auto_approve_on_timeout: false,
                    resolved_at: None,
                    resolved_by: None,
                    approved_args: None,
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
    ///
    /// Transitions the effect from `pending` to `approved`, resumes the
    /// session, and returns the execution data the proxy needs to run the
    /// tool. When `approved_args` differs from the original `proposed_args`,
    /// the effect's `args_snapshot` is updated to keep the audit trail accurate.
    #[instrument(skip(self), fields(org_id = %org_id, approval_request_id = %approval_request_id, actor = %actor))]
    pub async fn approve(
        &self,
        org_id: &OrgId,
        approval_request_id: &ApprovalRequestId,
        actor: &str,
        approved_args: Option<serde_json::Value>,
    ) -> Result<ApprovalResult, UndoLogError> {
        // Load the approval request to resolve args and get execution data.
        let approval =
            self.approval_store.get(org_id, approval_request_id).await?.ok_or_else(|| {
                UndoLogError::ApprovalNotFound { approval_id: approval_request_id.to_string() }
            })?;

        // Resolve args: approved_args (from the approver) override proposed_args.
        let resolved_args = approved_args.clone().unwrap_or(approval.proposed_args.clone());

        // All state mutations run in a single DB transaction for atomicity.
        let mut tx = self.approval_store.begin().await?;

        // Resolve the approval request.
        self.approval_store
            .resolve(
                &mut tx,
                org_id,
                approval_request_id,
                &ApprovalAction::Approve,
                actor,
                approved_args,
                None,
            )
            .await?;

        // Transition effect: pending -> approved.
        self.effect_store.approve_effect(&mut tx, org_id, &approval.effect_id).await?;

        // Load effect record to get tool_version for the response.
        let tool_version = self
            .effect_store
            .find_by_effect_id(&mut tx, org_id, &approval.effect_id)
            .await?
            .map(|e| e.tool_version)
            .unwrap_or_default();

        // If the approver modified the args, update args_snapshot for audit.
        if resolved_args != approval.proposed_args {
            self.effect_store
                .update_args_snapshot(&mut tx, org_id, &approval.effect_id, &resolved_args)
                .await?;
        }

        // Resume the session: awaiting_approval -> active.
        self.session_store.set_active(&mut tx, org_id, &approval.session_id).await?;

        tx.commit().await?;

        info!(
            approval_request_id = %approval_request_id,
            effect_id = %approval.effect_id,
            "Approval granted; effect transitioning to approved"
        );

        Ok(ApprovalResult {
            effect_id: approval.effect_id,
            session_id: approval.session_id,
            tool_name: approval.tool_name,
            tool_version,
            args: resolved_args,
        })
    }

    /// Reject an Irreversible action (human has denied permission).
    ///
    /// Transitions the effect from `pending` to `rejected` so the effect
    /// log accurately reflects the outcome.
    #[instrument(skip(self), fields(org_id = %org_id, approval_request_id = %approval_request_id, actor = %actor))]
    pub async fn reject(
        &self,
        org_id: &OrgId,
        approval_request_id: &ApprovalRequestId,
        actor: &str,
    ) -> Result<(), UndoLogError> {
        // Load approval first to get effect_id and session_id before mutating state.
        let approval =
            self.approval_store.get(org_id, approval_request_id).await?.ok_or_else(|| {
                UndoLogError::ApprovalNotFound { approval_id: approval_request_id.to_string() }
            })?;

        // All state mutations run in a single DB transaction for atomicity.
        let mut tx = self.approval_store.begin().await?;

        // Resolve the approval request with Reject action.
        self.approval_store
            .resolve(
                &mut tx,
                org_id,
                approval_request_id,
                &ApprovalAction::Reject,
                actor,
                None,
                None,
            )
            .await?;

        // Transition effect: pending -> rejected.
        self.effect_store.reject_effect(&mut tx, org_id, &approval.effect_id).await?;

        // Halt the session: a rejected action makes the trajectory untrustworthy.
        self.session_store
            .set_halted_with_conn(&mut tx, org_id, &approval.session_id, "approval rejected")
            .await?;

        tx.commit().await?;

        info!(approval_request_id = %approval_request_id, "Approval rejected");

        Ok(())
    }

    /// List the unresolved approval requests for an organization.
    ///
    /// The proxy calls this on startup and periodically to reconcile its
    /// in-memory approval view, so a proxy restart does not orphan approvals.
    #[instrument(skip(self), fields(org_id = %org_id))]
    pub async fn list_pending_approvals(
        &self,
        org_id: &OrgId,
    ) -> Result<Vec<ApprovalRequest>, UndoLogError> {
        self.approval_store.list_pending(org_id).await
    }

    // ── Private helpers ────────────────────────────────────────────────────

    /// Resolve the tool's tier from the registry.
    ///
    /// Falls back to `Safe` if the tool is not registered. When no version
    /// is specified and the exact match fails, falls back to a version-less
    /// lookup so that callers using the raw API without a tool_version still
    /// find the registered tool.
    async fn resolve_tier(&self, org_id: &OrgId, tool_name: &str, tool_version: &str) -> ToolTier {
        let registry = self.registry.read().await;
        if let Some(registration) = registry.get(org_id, tool_name, tool_version).await {
            return registration.tier;
        }
        if tool_version.is_empty() {
            if let Some(registration) = registry.find_by_name(org_id, tool_name).await {
                return registration.tier;
            }
        }
        debug!(
            tool = %tool_name,
            version = %tool_version,
            "Tool not registered; defaulting to Safe tier"
        );
        ToolTier::Safe
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
