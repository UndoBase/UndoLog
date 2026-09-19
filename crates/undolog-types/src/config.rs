//! Configuration types for effect retention policy.

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
