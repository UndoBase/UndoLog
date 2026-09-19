//! In-memory session state cache.
//!
//! Avoids a PostgreSQL round-trip on every `intercept` call by keeping
//! recently accessed session records in memory. Entries expire after a
//! configurable TTL.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use tokio::sync::RwLock;
use tracing::{debug, instrument};

use undolog_types::{
    config::CacheConfig,
    ids::{OrgId, SessionId},
    session::SessionRecord,
};

/// Composite key for session cache lookups.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct SessionKey {
    org_id: OrgId,
    session_id: SessionId,
}

/// A cached entry with its insertion timestamp for TTL tracking.
#[derive(Debug, Clone)]
struct CacheEntry {
    record: SessionRecord,
    inserted_at: Instant,
}

/// Shared interior state for the session cache.
struct CacheInner {
    entries: RwLock<HashMap<SessionKey, CacheEntry>>,
    config: CacheConfig,
}

/// In-memory cache for session state records.
///
/// The cache is safe for concurrent access via `tokio::sync::RwLock`.
/// Read-heavy workloads (the common case for `intercept`) acquire a
/// shared lock; writes (insertions and evictions) acquire an exclusive lock.
///
/// Cloning a `SessionCache` shares the same underlying state.
#[derive(Clone)]
pub struct SessionCache {
    inner: Arc<CacheInner>,
}

impl SessionCache {
    /// Create a new session cache with the given configuration.
    pub fn new(config: CacheConfig) -> Self {
        Self {
            inner: Arc::new(CacheInner {
                entries: RwLock::new(HashMap::with_capacity(config.max_entries.min(1024))),
                config,
            }),
        }
    }

    /// Look up a session record by org and session ID.
    ///
    /// Returns `Some(record)` if the entry exists and has not expired.
    /// Expired entries are evicted on access and return `None`.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn get(&self, org_id: &OrgId, session_id: &SessionId) -> Option<SessionRecord> {
        let key = SessionKey { org_id: *org_id, session_id: *session_id };
        let ttl = self.inner.config.ttl_duration();

        // Fast path: read lock for cache hits (the common case).
        {
            let guard = self.inner.entries.read().await;
            if let Some(entry) = guard.get(&key) {
                if entry.inserted_at.elapsed() <= ttl {
                    debug!(
                        org_id = %org_id,
                        session_id = %session_id,
                        "Session cache hit"
                    );
                    return Some(entry.record.clone());
                }
            }
        }

        // Slow path: write lock to evict expired entry or record miss.
        let mut guard = self.inner.entries.write().await;
        if let Some(entry) = guard.get(&key) {
            if entry.inserted_at.elapsed() > ttl {
                debug!(
                    org_id = %org_id,
                    session_id = %session_id,
                    "Session cache: TTL expired, evicting"
                );
                guard.remove(&key);
            }
        }
        debug!(
            org_id = %org_id,
            session_id = %session_id,
            "Session cache miss"
        );
        None
    }

    /// Insert or update a session record in the cache.
    ///
    /// If the cache is at capacity, the oldest entry is evicted first.
    #[instrument(skip(self, record), fields(org_id = %record.org_id, session_id = %record.session_id))]
    pub async fn insert(&self, record: SessionRecord) {
        let key = SessionKey { org_id: record.org_id, session_id: record.session_id };
        let entry = CacheEntry { record, inserted_at: Instant::now() };

        let mut guard = self.inner.entries.write().await;

        // Evict oldest if at capacity and inserting a new key.
        if self.inner.config.max_entries > 0
            && !guard.contains_key(&key)
            && guard.len() >= self.inner.config.max_entries
        {
            if let Some(oldest_key) =
                guard.iter().min_by_key(|(_, e)| e.inserted_at).map(|(k, _)| k.clone())
            {
                debug!("Session cache: evicting oldest entry at capacity");
                guard.remove(&oldest_key);
            }
        }

        guard.insert(key, entry);
    }

    /// Remove a session record from the cache.
    ///
    /// Called on state transitions that invalidate the cached state.
    #[instrument(skip(self), fields(org_id = %org_id, session_id = %session_id))]
    pub async fn invalidate(&self, org_id: &OrgId, session_id: &SessionId) {
        let key = SessionKey { org_id: *org_id, session_id: *session_id };
        let mut guard = self.inner.entries.write().await;
        let removed = guard.remove(&key).is_some();
        if removed {
            debug!(
                org_id = %org_id,
                session_id = %session_id,
                "Session cache: invalidated"
            );
        }
    }

    /// Return the number of entries currently in the cache.
    pub async fn len(&self) -> usize {
        self.inner.entries.read().await.len()
    }

    /// Return `true` if the cache contains no entries.
    pub async fn is_empty(&self) -> bool {
        self.inner.entries.read().await.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use undolog_types::session::SessionState;

    fn make_record(org_id: OrgId, session_id: SessionId) -> SessionRecord {
        SessionRecord {
            session_id,
            org_id,
            project_id: None,
            external_run_id: None,
            agent_name: None,
            state: SessionState::Active,
            tool_calls_total: 0,
            compensations_total: 0,
            approvals_pending: 0,
            started_at: Utc::now(),
            completed_at: None,
            failed_at: None,
            failure_reason: None,
            metadata: serde_json::json!({}),
        }
    }

    #[tokio::test]
    async fn cache_hit_returns_record() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        let org = OrgId::new();
        let session = SessionId::new();
        let record = make_record(org, session);

        cache.insert(record).await;
        let hit = cache.get(&org, &session).await;
        assert!(hit.is_some());
        assert_eq!(hit.unwrap().session_id, session);
    }

    #[tokio::test]
    async fn cache_miss_returns_none() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        let org = OrgId::new();
        let session = SessionId::new();

        assert!(cache.get(&org, &session).await.is_none());
    }

    #[tokio::test]
    async fn invalidate_removes_entry() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        let org = OrgId::new();
        let session = SessionId::new();
        let record = make_record(org, session);

        cache.insert(record).await;
        assert!(cache.get(&org, &session).await.is_some());

        cache.invalidate(&org, &session).await;
        assert!(cache.get(&org, &session).await.is_none());
    }

    #[tokio::test]
    async fn insert_overwrites_existing() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        let org = OrgId::new();
        let session = SessionId::new();

        let mut record = make_record(org, session);
        cache.insert(record.clone()).await;

        record.tool_calls_total = 5;
        cache.insert(record).await;

        let cached = cache.get(&org, &session).await.unwrap();
        assert_eq!(cached.tool_calls_total, 5);
    }

    #[tokio::test]
    async fn len_and_is_empty() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        assert!(cache.is_empty().await);
        assert_eq!(cache.len().await, 0);

        let org = OrgId::new();
        let session = SessionId::new();
        cache.insert(make_record(org, session)).await;

        assert!(!cache.is_empty().await);
        assert_eq!(cache.len().await, 1);
    }

    #[tokio::test]
    async fn capacity_eviction() {
        let cache = SessionCache::new(CacheConfig::new(60, 2));
        let org = OrgId::new();

        let s1 = SessionId::new();
        let s2 = SessionId::new();
        let s3 = SessionId::new();

        cache.insert(make_record(org, s1)).await;
        cache.insert(make_record(org, s2)).await;
        assert_eq!(cache.len().await, 2);

        // Inserting a third should evict the oldest (s1).
        cache.insert(make_record(org, s3)).await;
        assert_eq!(cache.len().await, 2);
        assert!(cache.get(&org, &s1).await.is_none());
        assert!(cache.get(&org, &s2).await.is_some());
        assert!(cache.get(&org, &s3).await.is_some());
    }

    #[tokio::test]
    async fn same_key_does_not_trigger_eviction() {
        let cache = SessionCache::new(CacheConfig::new(60, 2));
        let org = OrgId::new();
        let session = SessionId::new();

        cache.insert(make_record(org, session)).await;
        cache.insert(make_record(org, session)).await;
        assert_eq!(cache.len().await, 1);
    }

    #[tokio::test]
    async fn invalidate_nonexistent_is_noop() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        let org = OrgId::new();
        let session = SessionId::new();
        // Should not panic.
        cache.invalidate(&org, &session).await;
        assert!(cache.is_empty().await);
    }

    #[tokio::test]
    async fn multiple_orgs_are_isolated() {
        let cache = SessionCache::new(CacheConfig::new(60, 100));
        let org1 = OrgId::new();
        let org2 = OrgId::new();
        let session = SessionId::new();

        cache.insert(make_record(org1, session)).await;
        assert!(cache.get(&org1, &session).await.is_some());
        assert!(cache.get(&org2, &session).await.is_none());
    }

    #[tokio::test]
    async fn unlimited_capacity() {
        let cache = SessionCache::new(CacheConfig::new(60, 0));
        let org = OrgId::new();

        for _ in 0..200 {
            let s = SessionId::new();
            cache.insert(make_record(org, s)).await;
        }
        assert_eq!(cache.len().await, 200);
    }
}
