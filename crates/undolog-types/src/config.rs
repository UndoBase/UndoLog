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
