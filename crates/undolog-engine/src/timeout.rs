//! Approval timeout background processor.
//!
//! Periodically scans for pending approval requests that have exceeded
//! their configured timeout and transitions them to `timed_out` or
//! `auto_approved` states. Each processed request gets an audit event
//! recorded in `undolog_approval_events`.

use std::time::Duration;

use tracing::{info, warn};
use undolog_types::config::ApprovalTimeoutConfig;

#[cfg(all(feature = "sqlite", not(feature = "pg")))]
use undolog_store::sqlite::ApprovalStore;
#[cfg(feature = "pg")]
use undolog_store::ApprovalStore;

/// Spawn a background task that periodically processes timed-out approvals.
///
/// The task iterates over all organisations with pending approvals and
/// calls `process_timeouts` to transition expired requests to `timed_out`
/// or `auto_approved` states. Each processed request gets an audit event
/// recorded in `undolog_approval_events`.
///
/// Returns a [`tokio::task::JoinHandle`] so the caller can abort the
/// task on shutdown if needed.
pub fn spawn_timeout_processor(
    approval_store: ApprovalStore,
    config: &ApprovalTimeoutConfig,
) -> tokio::task::JoinHandle<()> {
    let interval = config.check_interval();
    tokio::spawn(async move {
        run_timeout_loop(approval_store, interval).await;
    })
}

/// The core timeout processing loop.
///
/// Runs forever, ticking at the given interval. Called by
/// [`spawn_timeout_processor`] after spawning the background task.
pub async fn run_timeout_loop(approval_store: ApprovalStore, interval: Duration) {
    let mut ticker = tokio::time::interval(interval);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        ticker.tick().await;
        process_pending_timeouts(&approval_store).await;
    }
}

/// Process all pending timeouts across all organisations.
///
/// This is the single-tick logic: list orgs with pending approvals,
/// then call `process_timeouts` for each. Errors are logged but do
/// not terminate the loop.
pub async fn process_pending_timeouts(approval_store: &ApprovalStore) {
    match approval_store.list_orgs_with_pending_approvals().await {
        Ok(org_ids) => {
            for org_id in &org_ids {
                match approval_store.process_timeouts(org_id).await {
                    Ok(count) if count > 0 => {
                        info!(
                            org_id = %org_id,
                            processed = count,
                            "Approval timeout processor: handled timed-out approvals"
                        );
                    }
                    Ok(_) => {}
                    Err(e) => {
                        warn!(
                            org_id = %org_id,
                            error = %e,
                            "Approval timeout processor: failed to process timeouts"
                        );
                    }
                }
            }
        }
        Err(e) => {
            warn!(
                error = %e,
                "Approval timeout processor: failed to list orgs"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_check_interval_conversion() {
        let config = ApprovalTimeoutConfig::new(3600, false, 45);
        let interval = config.check_interval();
        assert_eq!(interval, Duration::from_secs(45));
    }
}
