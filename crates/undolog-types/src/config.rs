//! Configuration types for effect retention policy and approval timeouts.

use chrono::Duration;
use serde::{Deserialize, Serialize};

/// Retention policy configuration for the effect log.
///
/// Controls how long effects are retained before being eligible for deletion.
/// Each organization can have its own retention policy.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RetentionConfig {
    /// Time-to-live for effects in days.
    ///
    /// Effects older than this are eligible for deletion.
    /// Minimum value is 1 day.
    pub ttl_days: i64,
}

impl RetentionConfig {
    /// Create a new retention configuration.
    ///
    /// # Arguments
    ///
    /// * `ttl_days` - Time-to-live in days (minimum 1)
    ///
    /// # Panics
    ///
    /// Panics if `ttl_days` is less than 1.
    pub fn new(ttl_days: i64) -> Self {
        assert!(ttl_days >= 1, "TTL must be at least 1 day");
        Self { ttl_days }
    }

    /// Return the TTL as a chrono Duration.
    pub fn ttl_duration(&self) -> Duration {
        Duration::days(self.ttl_days)
    }

    /// Return the cutoff timestamp before which effects are eligible for deletion.
    ///
    /// # Arguments
    ///
    /// * `now` - Current timestamp
    pub fn cutoff(&self, now: chrono::DateTime<chrono::Utc>) -> chrono::DateTime<chrono::Utc> {
        now - self.ttl_duration()
    }
}

impl Default for RetentionConfig {
    /// Default retention: 90 days.
    fn default() -> Self {
        Self::new(90)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_retention_config_new() {
        let config = RetentionConfig::new(30);
        assert_eq!(config.ttl_days, 30);
    }

    #[test]
    fn test_retention_config_default() {
        let config = RetentionConfig::default();
        assert_eq!(config.ttl_days, 90);
    }

    #[test]
    fn test_retention_config_ttl_duration() {
        let config = RetentionConfig::new(7);
        let duration = config.ttl_duration();
        assert_eq!(duration, Duration::days(7));
    }

    #[test]
    fn test_retention_config_cutoff() {
        let config = RetentionConfig::new(30);
        let now = chrono::Utc::now();
        let cutoff = config.cutoff(now);
        let expected = now - Duration::days(30);
        assert_eq!(cutoff, expected);
    }

    #[test]
    #[should_panic(expected = "TTL must be at least 1 day")]
    fn test_retention_config_zero_ttl_panics() {
        RetentionConfig::new(0);
    }

    #[test]
    #[should_panic(expected = "TTL must be at least 1 day")]
    fn test_retention_config_negative_ttl_panics() {
        RetentionConfig::new(-1);
    }
}

// ── Approval Timeout Configuration ──────────────────────────────────────────

/// Configuration for the approval timeout background processor.
///
/// Controls how pending approval requests are handled when they exceed
/// their timeout window. The processor runs on a fixed interval and
/// transitions expired requests to either `timed_out` or `auto_approved`
/// depending on the `auto_approve` policy.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ApprovalTimeoutConfig {
    /// Time in seconds after which a pending approval is considered timed out.
    ///
    /// Must be at least 1 second. Default is 86400 (24 hours).
    pub timeout_secs: i64,

    /// Whether to auto-approve approvals that time out.
    ///
    /// When `true`, timed-out approvals transition to `auto_approved`.
    /// When `false`, they transition to `timed_out` (rejected).
    /// Default is `false`.
    pub auto_approve: bool,

    /// How often the background processor checks for timed-out approvals, in seconds.
    ///
    /// Must be at least 1 second. Default is 60 seconds.
    pub check_interval_secs: u64,
}

impl ApprovalTimeoutConfig {
    /// Create a new approval timeout configuration.
    ///
    /// # Arguments
    ///
    /// * `timeout_secs` - Timeout duration in seconds (minimum 1)
    /// * `auto_approve` - Whether to auto-approve timed-out requests
    /// * `check_interval_secs` - Check interval in seconds (minimum 1)
    ///
    /// # Panics
    ///
    /// Panics if `timeout_secs` or `check_interval_secs` is less than 1.
    pub fn new(timeout_secs: i64, auto_approve: bool, check_interval_secs: u64) -> Self {
        assert!(timeout_secs >= 1, "timeout_secs must be at least 1");
        assert!(check_interval_secs >= 1, "check_interval_secs must be at least 1");
        Self { timeout_secs, auto_approve, check_interval_secs }
    }

    /// Return the timeout as a chrono Duration.
    pub fn timeout_duration(&self) -> Duration {
        Duration::seconds(self.timeout_secs)
    }

    /// Return the cutoff timestamp before which approvals are considered timed out.
    ///
    /// # Arguments
    ///
    /// * `now` - Current timestamp
    pub fn cutoff(&self, now: chrono::DateTime<chrono::Utc>) -> chrono::DateTime<chrono::Utc> {
        now - self.timeout_duration()
    }

    /// Return the check interval as a `std::time::Duration`.
    pub fn check_interval(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.check_interval_secs)
    }
}

impl Default for ApprovalTimeoutConfig {
    /// Default: 24-hour timeout, no auto-approve, check every 60 seconds.
    fn default() -> Self {
        Self::new(86400, false, 60)
    }
}

#[cfg(test)]
mod approval_timeout_tests {
    use super::*;

    #[test]
    fn test_approval_timeout_config_new() {
        let config = ApprovalTimeoutConfig::new(3600, true, 30);
        assert_eq!(config.timeout_secs, 3600);
        assert!(config.auto_approve);
        assert_eq!(config.check_interval_secs, 30);
    }

    #[test]
    fn test_approval_timeout_config_default() {
        let config = ApprovalTimeoutConfig::default();
        assert_eq!(config.timeout_secs, 86400);
        assert!(!config.auto_approve);
        assert_eq!(config.check_interval_secs, 60);
    }

    #[test]
    fn test_approval_timeout_duration() {
        let config = ApprovalTimeoutConfig::new(3600, false, 60);
        let duration = config.timeout_duration();
        assert_eq!(duration, Duration::seconds(3600));
    }

    #[test]
    fn test_approval_timeout_cutoff() {
        let config = ApprovalTimeoutConfig::new(3600, false, 60);
        let now = chrono::Utc::now();
        let cutoff = config.cutoff(now);
        let expected = now - Duration::seconds(3600);
        assert_eq!(cutoff, expected);
    }

    #[test]
    fn test_approval_timeout_check_interval() {
        let config = ApprovalTimeoutConfig::new(86400, false, 30);
        let interval = config.check_interval();
        assert_eq!(interval, std::time::Duration::from_secs(30));
    }

    #[test]
    #[should_panic(expected = "timeout_secs must be at least 1")]
    fn test_approval_timeout_zero_timeout_panics() {
        ApprovalTimeoutConfig::new(0, false, 60);
    }

    #[test]
    #[should_panic(expected = "timeout_secs must be at least 1")]
    fn test_approval_timeout_negative_timeout_panics() {
        ApprovalTimeoutConfig::new(-1, false, 60);
    }

    #[test]
    #[should_panic(expected = "check_interval_secs must be at least 1")]
    fn test_approval_timeout_zero_interval_panics() {
        ApprovalTimeoutConfig::new(86400, false, 0);
    }

    #[test]
    fn test_approval_timeout_config_serialization() {
        let config = ApprovalTimeoutConfig::new(3600, true, 30);
        let json = serde_json::to_string(&config).unwrap();
        let deserialized: ApprovalTimeoutConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, deserialized);
    }
}

// ── Session Cache Configuration ───────────────────────────────────────────────

/// Configuration for the in-memory session state cache.
///
/// The cache avoids a PostgreSQL round-trip on every `intercept` call by
/// keeping recently accessed session records in memory. Entries expire
/// after `ttl_secs` seconds of inactivity.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CacheConfig {
    /// Time-to-live for cached entries in seconds.
    ///
    /// Entries older than this are evicted on the next access attempt.
    /// Minimum value is 1 second. Default is 300 seconds (5 minutes).
    pub ttl_secs: u64,

    /// Maximum number of entries in the cache.
    ///
    /// When the capacity is reached the oldest entry is evicted regardless
    /// of TTL. Zero means unlimited. Default is 10000.
    pub max_entries: usize,
}

impl CacheConfig {
    /// Create a new cache configuration.
    ///
    /// # Arguments
    ///
    /// * `ttl_secs` - Time-to-live in seconds (minimum 1)
    /// * `max_entries` - Maximum number of cached entries (0 for unlimited)
    ///
    /// # Panics
    ///
    /// Panics if `ttl_secs` is less than 1.
    pub fn new(ttl_secs: u64, max_entries: usize) -> Self {
        assert!(ttl_secs >= 1, "ttl_secs must be at least 1");
        Self { ttl_secs, max_entries }
    }

    /// Return the TTL as a `std::time::Duration`.
    pub fn ttl_duration(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.ttl_secs)
    }
}

impl Default for CacheConfig {
    /// Default: 5-minute TTL, 10000 max entries.
    fn default() -> Self {
        Self::new(300, 10000)
    }
}

#[cfg(test)]
mod cache_config_tests {
    use super::*;

    #[test]
    fn test_cache_config_new() {
        let config = CacheConfig::new(60, 500);
        assert_eq!(config.ttl_secs, 60);
        assert_eq!(config.max_entries, 500);
    }

    #[test]
    fn test_cache_config_default() {
        let config = CacheConfig::default();
        assert_eq!(config.ttl_secs, 300);
        assert_eq!(config.max_entries, 10000);
    }

    #[test]
    fn test_cache_config_ttl_duration() {
        let config = CacheConfig::new(120, 0);
        assert_eq!(config.ttl_duration(), std::time::Duration::from_secs(120));
    }

    #[test]
    fn test_cache_config_unlimited_entries() {
        let config = CacheConfig::new(60, 0);
        assert_eq!(config.max_entries, 0);
    }

    #[test]
    #[should_panic(expected = "ttl_secs must be at least 1")]
    fn test_cache_config_zero_ttl_panics() {
        CacheConfig::new(0, 100);
    }

    #[test]
    fn test_cache_config_serialization() {
        let config = CacheConfig::new(600, 5000);
        let json = serde_json::to_string(&config).unwrap();
        let deserialized: CacheConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, deserialized);
    }
}

// ── Rate Limit Configuration ────────────────────────────────────────────────

/// Configuration for circuit breaker and concurrency limiting.
///
/// The circuit breaker tracks error rates and opens (rejects all requests)
/// when the threshold is exceeded. After a cooldown period it half-opens
/// and allows a single test request through.
///
/// The concurrency limiter caps the number of concurrent intercept calls
/// using a bounded semaphore. Extra requests are rejected immediately.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RateLimitConfig {
    /// Number of consecutive errors that trip the circuit breaker.
    ///
    /// Must be at least 1. Default is 5.
    pub error_threshold: u32,

    /// Seconds to wait before transitioning from Open to HalfOpen.
    ///
    /// Must be at least 1. Default is 30.
    pub cooldown_secs: u64,

    /// Maximum number of concurrent intercept calls.
    ///
    /// Must be at least 1. Default is 100.
    pub max_concurrency: usize,
}

impl RateLimitConfig {
    /// Create a new rate limit configuration.
    ///
    /// # Arguments
    ///
    /// * `error_threshold` - Consecutive errors before the circuit opens (minimum 1)
    /// * `cooldown_secs` - Cooldown period in seconds (minimum 1)
    /// * `max_concurrency` - Maximum concurrent requests (minimum 1)
    ///
    /// # Panics
    ///
    /// Panics if any parameter is less than its minimum.
    pub fn new(error_threshold: u32, cooldown_secs: u64, max_concurrency: usize) -> Self {
        assert!(error_threshold >= 1, "error_threshold must be at least 1");
        assert!(cooldown_secs >= 1, "cooldown_secs must be at least 1");
        assert!(max_concurrency >= 1, "max_concurrency must be at least 1");
        Self { error_threshold, cooldown_secs, max_concurrency }
    }

    /// Return the cooldown as a `std::time::Duration`.
    pub fn cooldown_duration(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.cooldown_secs)
    }
}

impl Default for RateLimitConfig {
    /// Default: 5 errors trips breaker, 30s cooldown, 100 max concurrent.
    fn default() -> Self {
        Self::new(5, 30, 100)
    }
}

#[cfg(test)]
mod rate_limit_config_tests {
    use super::*;

    #[test]
    fn test_rate_limit_config_new() {
        let config = RateLimitConfig::new(10, 60, 50);
        assert_eq!(config.error_threshold, 10);
        assert_eq!(config.cooldown_secs, 60);
        assert_eq!(config.max_concurrency, 50);
    }

    #[test]
    fn test_rate_limit_config_default() {
        let config = RateLimitConfig::default();
        assert_eq!(config.error_threshold, 5);
        assert_eq!(config.cooldown_secs, 30);
        assert_eq!(config.max_concurrency, 100);
    }

    #[test]
    fn test_rate_limit_config_cooldown_duration() {
        let config = RateLimitConfig::new(3, 45, 10);
        assert_eq!(config.cooldown_duration(), std::time::Duration::from_secs(45));
    }

    #[test]
    #[should_panic(expected = "error_threshold must be at least 1")]
    fn test_rate_limit_config_zero_threshold_panics() {
        RateLimitConfig::new(0, 30, 100);
    }

    #[test]
    #[should_panic(expected = "cooldown_secs must be at least 1")]
    fn test_rate_limit_config_zero_cooldown_panics() {
        RateLimitConfig::new(5, 0, 100);
    }

    #[test]
    #[should_panic(expected = "max_concurrency must be at least 1")]
    fn test_rate_limit_config_zero_concurrency_panics() {
        RateLimitConfig::new(5, 30, 0);
    }

    #[test]
    fn test_rate_limit_config_serialization() {
        let config = RateLimitConfig::new(10, 60, 50);
        let json = serde_json::to_string(&config).unwrap();
        let deserialized: RateLimitConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(config, deserialized);
    }
}
