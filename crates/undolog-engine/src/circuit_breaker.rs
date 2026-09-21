//! Circuit breaker for protecting the engine from cascading failures.
//!
//! The circuit breaker tracks consecutive errors and opens (rejects all
//! requests) when the threshold is exceeded. After a cooldown period it
//! half-opens and allows a single test request through.
//!
//! States:
//! - **Closed**: Normal operation. Errors are counted.
//! - **Open**: All requests rejected with `CircuitBreakerOpen`.
//! - **HalfOpen**: One test request allowed. Success closes, failure re-opens.

use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Instant;

use tokio::sync::Mutex;
use tracing::{debug, warn};

/// The three states of the circuit breaker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CircuitState {
    /// Normal operation. Requests pass through.
    Closed,
    /// Too many errors. All requests are rejected.
    Open,
    /// Testing recovery. One request is allowed through.
    HalfOpen,
}

/// Internal state protected by the mutex.
struct Inner {
    state: CircuitState,
    opened_at: Option<Instant>,
}

/// A thread-safe circuit breaker that tracks consecutive errors.
///
/// Uses `AtomicU32` for the error counter and a single `Mutex<Inner>`
/// for state and timestamp, avoiding nested lock patterns.
pub struct CircuitBreaker {
    error_threshold: u32,
    cooldown: std::time::Duration,
    consecutive_errors: AtomicU32,
    inner: Mutex<Inner>,
}

impl CircuitBreaker {
    /// Create a new circuit breaker.
    ///
    /// # Arguments
    ///
    /// * `error_threshold` - Consecutive errors before the circuit opens.
    /// * `cooldown` - Duration to wait before half-opening.
    pub fn new(error_threshold: u32, cooldown: std::time::Duration) -> Self {
        Self {
            error_threshold,
            cooldown,
            consecutive_errors: AtomicU32::new(0),
            inner: Mutex::new(Inner { state: CircuitState::Closed, opened_at: None }),
        }
    }

    /// Check whether the circuit breaker allows the request.
    ///
    /// Returns `Ok(())` if the request is allowed, or
    /// `Err(UndoLogError::CircuitBreakerOpen)` if the circuit is open.
    pub async fn check(&self) -> Result<(), undolog_types::errors::UndoLogError> {
        let mut inner = self.inner.lock().await;

        match inner.state {
            CircuitState::Closed => Ok(()),
            CircuitState::Open => {
                if let Some(opened) = inner.opened_at {
                    if opened.elapsed() >= self.cooldown {
                        debug!("circuit breaker: Open -> HalfOpen (cooldown elapsed)");
                        inner.state = CircuitState::HalfOpen;
                        return Ok(());
                    }
                }
                warn!("circuit breaker: request rejected (Open)");
                Err(undolog_types::errors::UndoLogError::CircuitBreakerOpen)
            }
            CircuitState::HalfOpen => {
                debug!("circuit breaker: HalfOpen, allowing test request");
                Ok(())
            }
        }
    }

    /// Record a successful call. Resets the error counter and closes
    /// the circuit if it was half-open.
    pub async fn record_success(&self) {
        self.consecutive_errors.store(0, Ordering::Relaxed);
        let mut inner = self.inner.lock().await;
        if inner.state == CircuitState::HalfOpen {
            debug!("circuit breaker: HalfOpen -> Closed (success)");
            inner.state = CircuitState::Closed;
            inner.opened_at = None;
        }
    }

    /// Record a failed call. Increments the error counter and opens
    /// the circuit if the threshold is exceeded.
    pub async fn record_failure(&self) {
        let prev = self.consecutive_errors.fetch_add(1, Ordering::Relaxed);
        let current = prev + 1;

        let mut inner = self.inner.lock().await;
        match inner.state {
            CircuitState::HalfOpen => {
                warn!("circuit breaker: HalfOpen -> Open (failure, {} consecutive)", current,);
                inner.state = CircuitState::Open;
                inner.opened_at = Some(Instant::now());
            }
            CircuitState::Closed if current >= self.error_threshold => {
                warn!(
                    "circuit breaker: Closed -> Open ({} consecutive errors >= threshold {})",
                    current, self.error_threshold,
                );
                inner.state = CircuitState::Open;
                inner.opened_at = Some(Instant::now());
            }
            _ => {
                debug!("circuit breaker: error {}/{}", current, self.error_threshold,);
            }
        }
    }

    /// Return the current circuit state.
    pub async fn state(&self) -> CircuitState {
        self.inner.lock().await.state
    }

    /// Return the current consecutive error count.
    pub fn consecutive_errors(&self) -> u32 {
        self.consecutive_errors.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn closed_allows_requests() {
        let cb = CircuitBreaker::new(3, std::time::Duration::from_secs(1));
        assert!(cb.check().await.is_ok());
    }

    #[tokio::test]
    async fn opens_after_threshold() {
        let cb = CircuitBreaker::new(2, std::time::Duration::from_secs(60));
        cb.record_failure().await;
        assert_eq!(cb.state().await, CircuitState::Closed);

        cb.record_failure().await;
        assert_eq!(cb.state().await, CircuitState::Open);

        assert!(cb.check().await.is_err());
    }

    #[tokio::test]
    async fn success_resets_counter() {
        let cb = CircuitBreaker::new(3, std::time::Duration::from_secs(60));
        cb.record_failure().await;
        cb.record_failure().await;
        cb.record_success().await;
        assert_eq!(cb.consecutive_errors(), 0);
        assert_eq!(cb.state().await, CircuitState::Closed);
    }

    #[tokio::test]
    async fn half_open_allows_one_request() {
        let cb = CircuitBreaker::new(1, std::time::Duration::from_millis(50));
        cb.record_failure().await;
        assert_eq!(cb.state().await, CircuitState::Open);

        tokio::time::sleep(std::time::Duration::from_millis(60)).await;
        assert!(cb.check().await.is_ok());
        assert_eq!(cb.state().await, CircuitState::HalfOpen);
    }

    #[tokio::test]
    async fn half_open_success_closes() {
        let cb = CircuitBreaker::new(1, std::time::Duration::from_millis(50));
        cb.record_failure().await;
        tokio::time::sleep(std::time::Duration::from_millis(60)).await;
        let _ = cb.check().await;
        assert_eq!(cb.state().await, CircuitState::HalfOpen);

        cb.record_success().await;
        assert_eq!(cb.state().await, CircuitState::Closed);
    }

    #[tokio::test]
    async fn half_open_failure_reopens() {
        let cb = CircuitBreaker::new(1, std::time::Duration::from_millis(50));
        cb.record_failure().await;
        tokio::time::sleep(std::time::Duration::from_millis(60)).await;
        let _ = cb.check().await;
        assert_eq!(cb.state().await, CircuitState::HalfOpen);

        cb.record_failure().await;
        assert_eq!(cb.state().await, CircuitState::Open);
    }

    #[tokio::test]
    async fn open_rejects_until_cooldown() {
        let cb = CircuitBreaker::new(1, std::time::Duration::from_millis(100));
        cb.record_failure().await;
        assert!(cb.check().await.is_err());

        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        assert!(cb.check().await.is_err());

        tokio::time::sleep(std::time::Duration::from_millis(60)).await;
        assert!(cb.check().await.is_ok());
    }
}
