//! Saga orchestration primitives.

use std::{sync::Arc, time::Duration};

use async_trait::async_trait;
use reqwest::Client;
use thiserror::Error;
use tracing::{debug, info, instrument, warn};
use undolog_store::{EffectStore, SessionStore};
use undolog_types::{
    errors::UndoLogError,
    ids::{EffectId, OrgId, SessionId, UndoId},
    saga::{SagaStepState, UndoEntry},
    session::{SessionRecord, SessionState},
};

use crate::compensation_runner;

/// Retry policy used when calling compensation endpoints.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RetryConfig {
    /// Maximum number of retry attempts after the initial call.
    pub max_retries: u32,
    /// Initial backoff delay before the first retry.
    pub initial_interval: Duration,
    /// Multiplier applied to each subsequent retry delay.
    pub multiplier: f64,
    /// Upper bound for the delay between retries.
    pub max_interval: Duration,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_retries: 3,
            initial_interval: Duration::from_millis(100),
            multiplier: 2.0,
            max_interval: Duration::from_secs(5),
        }
    }
}

/// Registry used to resolve compensation endpoint keys to full URLs.
pub trait CompensationRegistry: Send + Sync {
    /// Resolve one compensation key to a request URL.
    fn endpoint_for(&self, key: &str) -> Option<String>;

    /// Return the default retry policy for compensation calls.
    fn default_retry_config(&self) -> RetryConfig;
}

/// Storage interface used by the saga orchestrator.
#[async_trait]
pub trait SagaStore: Send + Sync {
    /// Load one session record.
    async fn load_session(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<Option<SessionRecord>, SagaError>;

    /// Load the undo stack for a session in LIFO order.
    async fn load_undo_stack(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<Vec<UndoEntry>, SagaError>;

    /// Mark one undo entry as compensated.
    async fn mark_entry_compensated(&self, org_id: OrgId, undo_id: UndoId)
        -> Result<(), SagaError>;

    /// Mark one undo entry as permanently failed.
    async fn mark_entry_compensation_failed(
        &self,
        org_id: OrgId,
        undo_id: UndoId,
        reason: &str,
    ) -> Result<(), SagaError>;

    /// Mark the session as fully compensated.
    async fn mark_session_compensated(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<(), SagaError>;

    /// Mark the session as halted after a permanent compensation failure.
    async fn mark_session_halted(
        &self,
        org_id: OrgId,
        session_id: SessionId,
        reason: &str,
    ) -> Result<(), SagaError>;

    /// Mark the associated effect as compensation_failed.
    async fn mark_effect_compensation_failed(
        &self,
        org_id: OrgId,
        effect_id: EffectId,
    ) -> Result<(), SagaError>;
}

/// Database-backed saga store composed of the existing session and effect stores.
#[derive(Clone)]
pub struct DatabaseSagaStore {
    effects: EffectStore,
    sessions: SessionStore,
}

impl DatabaseSagaStore {
    /// Create a store adapter from the existing store types.
    pub fn new(effects: EffectStore, sessions: SessionStore) -> Self {
        Self { effects, sessions }
    }
}

#[async_trait]
impl SagaStore for DatabaseSagaStore {
    async fn load_session(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<Option<SessionRecord>, SagaError> {
        Ok(self.sessions.get_session(&org_id, &session_id).await?)
    }

    async fn load_undo_stack(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<Vec<UndoEntry>, SagaError> {
        Ok(self.effects.load_undo_stack(&org_id, &session_id).await?)
    }

    async fn mark_entry_compensated(
        &self,
        org_id: OrgId,
        undo_id: UndoId,
    ) -> Result<(), SagaError> {
        Ok(self.effects.mark_compensated(&org_id, &undo_id).await?)
    }

    async fn mark_entry_compensation_failed(
        &self,
        org_id: OrgId,
        undo_id: UndoId,
        reason: &str,
    ) -> Result<(), SagaError> {
        Ok(self.effects.mark_compensation_failed(&org_id, &undo_id, reason).await?)
    }

    async fn mark_session_compensated(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<(), SagaError> {
        Ok(self.sessions.set_compensated(&org_id, &session_id).await?)
    }

    async fn mark_session_halted(
        &self,
        org_id: OrgId,
        session_id: SessionId,
        reason: &str,
    ) -> Result<(), SagaError> {
        Ok(self.sessions.set_halted(&org_id, &session_id, reason).await?)
    }

    async fn mark_effect_compensation_failed(
        &self,
        org_id: OrgId,
        effect_id: EffectId,
    ) -> Result<(), SagaError> {
        Ok(self.effects.mark_effect_compensation_failed(&org_id, &effect_id).await?)
    }
}

/// Errors returned by the saga orchestrator.
#[derive(Debug, Error)]
pub enum SagaError {
    /// Underlying store failure.
    #[error("store error: {0}")]
    Store(#[from] UndoLogError),

    /// HTTP client failure.
    #[error("HTTP request failed: {0}")]
    Http(#[from] reqwest::Error),

    /// A compensation endpoint key could not be resolved.
    #[error("missing compensation endpoint for key {0}")]
    MissingCompensationEndpoint(String),

    /// The session was not found in the store.
    #[error("session not found")]
    SessionNotFound { session_id: SessionId },

    /// Compensation failed after all retries.
    #[error("compensation failed for undo {undo_id}: {reason}")]
    CompensationFailed { undo_id: UndoId, reason: String },
}

/// Coordinates session compensation against the store and registry.
#[derive(Clone)]
pub struct SagaOrchestrator {
    store: Arc<dyn SagaStore>,
    registry: Arc<dyn CompensationRegistry>,
    client: Client,
}

impl SagaOrchestrator {
    /// Create a new orchestrator using the default HTTP client.
    pub fn new(store: Arc<dyn SagaStore>, registry: Arc<dyn CompensationRegistry>) -> Self {
        Self::with_client(store, registry, Client::new())
    }

    /// Create a new orchestrator with a custom HTTP client.
    pub fn with_client(
        store: Arc<dyn SagaStore>,
        registry: Arc<dyn CompensationRegistry>,
        client: Client,
    ) -> Self {
        Self { store, registry, client }
    }

    /// Walk the undo stack for one session and compensate every pending step.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn compensate_session(
        &self,
        org_id: OrgId,
        session_id: SessionId,
    ) -> Result<SessionState, SagaError> {
        let session = self
            .store
            .load_session(org_id, session_id)
            .await?
            .ok_or(SagaError::SessionNotFound { session_id })?;

        if session.state.is_terminal() {
            info!(state = %session.state, "Session is already terminal");
            return Ok(session.state);
        }

        let undo_stack = self.store.load_undo_stack(org_id, session_id).await?;
        info!(undo_count = undo_stack.len(), state = %session.state, "Loaded undo stack");

        for entry in undo_stack {
            if matches!(
                entry.state,
                SagaStepState::Compensated | SagaStepState::Failed | SagaStepState::Skipped
            ) {
                debug!(undo_id = %entry.undo_id, state = ?entry.state, "Skipping terminal undo entry");
                continue;
            }

            match compensation_runner::execute_entry(
                &self.client,
                self.store.as_ref(),
                self.registry.as_ref(),
                &entry,
                org_id,
            )
            .await
            {
                Ok(()) => {
                    self.store.mark_entry_compensated(org_id, entry.undo_id).await?;
                }
                Err(SagaError::CompensationFailed { reason, .. }) => {
                    warn!(
                        undo_id = %entry.undo_id,
                        reason = %reason,
                        "Permanent compensation failure"
                    );
                    self.store.mark_session_halted(org_id, session_id, &reason).await?;
                    return Ok(SessionState::Halted);
                }
                Err(SagaError::MissingCompensationEndpoint(key)) => {
                    let reason = format!("missing compensation endpoint for key {key}");
                    warn!(
                        undo_id = %entry.undo_id,
                        reason = %reason,
                        "Permanent compensation failure"
                    );
                    self.store.mark_session_halted(org_id, session_id, &reason).await?;
                    return Ok(SessionState::Halted);
                }
                Err(err) => return Err(err),
            }
        }

        self.store.mark_session_compensated(org_id, session_id).await?;
        Ok(SessionState::Compensated)
    }
}
