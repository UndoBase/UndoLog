//! Concurrency limiter for bounding concurrent intercept calls.
//!
//! Uses a tokio `Semaphore` to cap the number of concurrent requests.
//! When the semaphore is exhausted, new requests are rejected with
//! `ConcurrencyLimitReached`.

use tokio::sync::Semaphore;
use tracing::debug;

use undolog_types::errors::UndoLogError;

/// A concurrency limiter backed by a tokio semaphore.
///
/// Wraps a `Semaphore` and provides a `try_acquire` method that returns
/// a typed error instead of a bare `TryAcquireError`.
pub struct ConcurrencyLimiter {
    semaphore: Semaphore,
    max_concurrency: usize,
}

impl ConcurrencyLimiter {
    /// Create a new limiter with the given concurrency cap.
    ///
    /// # Arguments
    ///
    /// * `max_concurrency` - Maximum number of concurrent permits.
    pub fn new(max_concurrency: usize) -> Self {
        Self { semaphore: Semaphore::new(max_concurrency), max_concurrency }
    }

    /// Try to acquire a permit without blocking.
    ///
    /// Returns `Ok(permit)` if a permit is available, or
    /// `Err(ConcurrencyLimitReached)` if all permits are in use.
    pub fn try_acquire(&self) -> Result<tokio::sync::SemaphorePermit<'_>, UndoLogError> {
        self.semaphore.try_acquire().map_err(|_| {
            debug!("concurrency limit reached: {} permits in use", self.max_concurrency,);
            UndoLogError::ConcurrencyLimitReached(self.max_concurrency)
        })
    }

    /// Return the number of available permits.
    pub fn available(&self) -> usize {
        self.semaphore.available_permits()
    }

    /// Return the maximum concurrency.
    pub fn max_concurrency(&self) -> usize {
        self.max_concurrency
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn acquire_succeeds_when_available() {
        let limiter = ConcurrencyLimiter::new(2);
        let _p1 = limiter.try_acquire().expect("first acquire");
        let _p2 = limiter.try_acquire().expect("second acquire");
        assert_eq!(limiter.available(), 0);
    }

    #[test]
    fn acquire_fails_when_exhausted() {
        let limiter = ConcurrencyLimiter::new(1);
        let _p1 = limiter.try_acquire().expect("first acquire");
        assert!(limiter.try_acquire().is_err());
    }

    #[test]
    fn permit_releases_on_drop() {
        let limiter = ConcurrencyLimiter::new(1);
        {
            let _p = limiter.try_acquire().expect("acquire");
            assert_eq!(limiter.available(), 0);
        }
        assert_eq!(limiter.available(), 1);
    }

    #[test]
    fn max_concurrency_returned() {
        let limiter = ConcurrencyLimiter::new(42);
        assert_eq!(limiter.max_concurrency(), 42);
    }
}
