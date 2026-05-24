//! HTTP execution for one undo stack entry.

use std::time::Duration;

use backoff::{backoff::Backoff, ExponentialBackoff};
use reqwest::{
    header::{HeaderName, HeaderValue},
    Client, StatusCode,
};
use tracing::{debug, info, instrument, warn};
use undolog_types::{ids::OrgId, saga::UndoEntry};

use crate::orchestrator::{CompensationRegistry, RetryConfig, SagaError, SagaStore};

/// Build the deterministic idempotency key for one undo entry.
pub fn idempotency_key_for(undo_id: &undolog_types::ids::UndoId) -> String {
    format!("undo-{undo_id}")
}

fn retry_policy(entry: &UndoEntry, registry: &dyn CompensationRegistry) -> RetryConfig {
    let default = registry.default_retry_config();

    RetryConfig {
        max_retries: if entry.compensation.max_retries == 0 {
            default.max_retries
        } else {
            entry.compensation.max_retries as u32
        },
        initial_interval: if entry.compensation.retry_backoff_ms == 0 {
            default.initial_interval
        } else {
            Duration::from_millis(entry.compensation.retry_backoff_ms as u64)
        },
        multiplier: default.multiplier,
        max_interval: default.max_interval,
    }
}

fn is_permanent_client_error(status: StatusCode) -> bool {
    status.is_client_error() && status != StatusCode::TOO_MANY_REQUESTS
}

/// Execute one compensation endpoint call and persist failure state if needed.
#[instrument(skip(client, store, registry, entry), fields(undo_id = %entry.undo_id, org_id = %org_id))]
#[allow(unused_assignments)]
pub(crate) async fn execute_entry(
    client: &Client,
    store: &dyn SagaStore,
    registry: &dyn CompensationRegistry,
    entry: &UndoEntry,
    org_id: OrgId,
) -> Result<(), SagaError> {
    let endpoint = match registry.endpoint_for(&entry.compensation.fn_name) {
        Some(endpoint) => endpoint,
        None => {
            let reason =
                format!("missing compensation endpoint for key {}", entry.compensation.fn_name);
            store.mark_entry_compensation_failed(org_id, entry.undo_id, &reason).await?;
            store.mark_effect_compensation_failed(org_id, entry.effect_id).await?;
            return Err(SagaError::MissingCompensationEndpoint(entry.compensation.fn_name.clone()));
        }
    };

    let idempotency_key = idempotency_key_for(&entry.undo_id);
    debug!(idempotency_key = %idempotency_key, "Derived compensation idempotency key");

    let retry = retry_policy(entry, registry);
    let mut backoff = ExponentialBackoff {
        initial_interval: retry.initial_interval,
        max_interval: retry.max_interval,
        multiplier: retry.multiplier,
        max_elapsed_time: None,
        ..ExponentialBackoff::default()
    };

    let header_name = HeaderName::from_static("idempotency-key");
    let header_value = match HeaderValue::from_str(&idempotency_key) {
        Ok(value) => value,
        Err(err) => {
            let reason = err.to_string();
            store.mark_entry_compensation_failed(org_id, entry.undo_id, &reason).await?;
            store.mark_effect_compensation_failed(org_id, entry.effect_id).await?;
            return Err(SagaError::CompensationFailed { undo_id: entry.undo_id, reason });
        }
    };

    let mut attempt = 0u32;
    let mut last_reason = String::new();

    loop {
        attempt += 1;
        info!(
            undo_id = %entry.undo_id,
            attempt,
            endpoint = %endpoint,
            "Sending compensation request"
        );

        let response = client
            .post(&endpoint)
            .header(header_name.clone(), header_value.clone())
            .json(&entry.compensation.args)
            .send()
            .await;

        match response {
            Ok(resp) if resp.status().is_success() => {
                info!(undo_id = %entry.undo_id, attempt, "Compensation succeeded");
                return Ok(());
            }
            Ok(resp) if is_permanent_client_error(resp.status()) => {
                last_reason = format!("endpoint returned {}", resp.status());
                warn!(undo_id = %entry.undo_id, attempt, reason = %last_reason, "Permanent compensation failure");
                break;
            }
            Ok(resp) => {
                last_reason = format!("endpoint returned {}", resp.status());
                warn!(undo_id = %entry.undo_id, attempt, reason = %last_reason, "Retryable compensation failure");
            }
            Err(err) => {
                last_reason = err.to_string();
                warn!(undo_id = %entry.undo_id, attempt, reason = %last_reason, "Retryable compensation error");
            }
        }

        if attempt > retry.max_retries {
            break;
        }

        if let Some(delay) = backoff.next_backoff() {
            tokio::time::sleep(delay).await;
        }
    }

    let reason = if last_reason.is_empty() {
        "compensation retries exhausted".to_string()
    } else {
        last_reason
    };

    store.mark_entry_compensation_failed(org_id, entry.undo_id, &reason).await?;
    store.mark_effect_compensation_failed(org_id, entry.effect_id).await?;

    Err(SagaError::CompensationFailed { undo_id: entry.undo_id, reason })
}
