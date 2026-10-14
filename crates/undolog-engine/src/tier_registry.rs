//! TierRegistry - in-memory lookup table for tool tier classifications.
//!
//! Loaded from `undolog_tool_registry` at startup and refreshed every
//! `refresh_interval` (default 15 s) to pick up new tool registrations
//! without a restart.
//!
//! All lookups are O(1). Interior `RwLock` allows unlimited concurrent
//! reads (the hot path) with infrequent write locks (refresh only).

use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::RwLock;
use tracing::{debug, info};
use undolog_types::{
    ids::{OrgId, ToolId},
    tier::ToolTier,
};

// ── Public types ──────────────────────────────────────────────────────────────

/// A cached tool registration entry.
#[derive(Debug, Clone)]
pub struct ToolRegistration {
    /// Unique tool identifier from the registry.
    pub tool_id: ToolId,
    /// Logical tool name.
    pub tool_name: String,
    /// Semver version at registration time.
    pub tool_version: String,
    /// Runtime tier classification.
    pub tier: ToolTier,
    /// Risk classification tags for the approval UI.
    pub risk_tags: Vec<String>,
    /// Human-readable description of the business impact.
    pub estimated_impact: Option<String>,
}

/// Thread-safe, in-memory tool tier registry.
///
/// Wrap in `Arc` (done inside [`EffectEngine`]) for cheap sharing across tasks.
#[derive(Clone)]
pub struct TierRegistry {
    inner: Arc<RwLock<Inner>>,
}

// ── Internal state ────────────────────────────────────────────────────────────

/// Key: (org_id_str, tool_name, tool_version)
type Key = (String, String, String);

struct Inner {
    /// org_id_str → Key → ToolRegistration
    by_org: HashMap<String, HashMap<Key, ToolRegistration>>,
}

// ── Implementation ────────────────────────────────────────────────────────────

impl TierRegistry {
    /// Create an empty registry.
    pub fn new() -> Self {
        Self { inner: Arc::new(RwLock::new(Inner { by_org: HashMap::new() })) }
    }

    /// Upsert a single tool registration.
    ///
    /// Called during the initial load and every periodic refresh.
    pub async fn upsert(&self, org_id: &OrgId, reg: ToolRegistration) {
        let key = (org_id.to_string(), reg.tool_name.clone(), reg.tool_version.clone());
        let mut inner = self.inner.write().await;
        inner.by_org.entry(org_id.to_string()).or_default().insert(key, reg);
    }

    /// Replace all registrations for an org atomically (used during refresh).
    pub async fn replace_org(&self, org_id: &OrgId, regs: Vec<ToolRegistration>) {
        let mut map: HashMap<Key, ToolRegistration> = HashMap::with_capacity(regs.len());
        for reg in regs {
            let key = (org_id.to_string(), reg.tool_name.clone(), reg.tool_version.clone());
            map.insert(key, reg);
        }
        let mut inner = self.inner.write().await;
        let count = map.len();
        inner.by_org.insert(org_id.to_string(), map);
        debug!(org_id = %org_id, count, "Registry refreshed for org");
    }

    /// Look up a tool registration. Returns `None` if not registered.
    pub async fn get(
        &self,
        org_id: &OrgId,
        tool_name: &str,
        tool_version: &str,
    ) -> Option<ToolRegistration> {
        let key = (org_id.to_string(), tool_name.to_string(), tool_version.to_string());
        let inner = self.inner.read().await;
        inner.by_org.get(&org_id.to_string()).and_then(|m| m.get(&key)).cloned()
    }

    /// Look up a tool registration by name alone, ignoring version.
    ///
    /// Used as a fallback when the caller does not supply a tool_version.
    /// Returns the first matching registration (arbitrary if multiple versions exist).
    pub async fn find_by_name(&self, org_id: &OrgId, tool_name: &str) -> Option<ToolRegistration> {
        let inner = self.inner.read().await;
        inner
            .by_org
            .get(&org_id.to_string())
            .and_then(|m| m.iter().find(|(k, _)| k.1 == tool_name).map(|(_, v)| v.clone()))
    }

    /// Total registered tool count across all orgs (for health checks).
    pub async fn total_count(&self) -> usize {
        let inner = self.inner.read().await;
        inner.by_org.values().map(|m| m.len()).sum()
    }

    /// Remove all registrations for an org (test helper).
    #[cfg(test)]
    pub async fn clear_org(&self, org_id: &OrgId) {
        let mut inner = self.inner.write().await;
        inner.by_org.remove(&org_id.to_string());
    }
}

impl Default for TierRegistry {
    fn default() -> Self {
        Self::new()
    }
}

// ── Last successful refresh ───────────────────────────────────────────────────

/// Timestamp of the last successful registry load or refresh, shared with the
/// engine health endpoint so staleness stays visible without a metrics
/// subsystem. The process serves one registry, so one timestamp suffices.
static LAST_REFRESH: Mutex<Option<Instant>> = Mutex::new(None);

/// Seconds since the last successful registry refresh.
///
/// Returns `None` until the first successful load, which the health payload
/// reports as a JSON `null`.
pub fn last_refresh_age_secs() -> Option<u64> {
    let last = LAST_REFRESH.lock().expect("last refresh lock poisoned");
    last.map(|at| at.elapsed().as_secs())
}

/// Records the current instant as the last successful registry refresh.
///
/// Called after a successful database load or periodic refresh. A failed
/// refresh leaves the previous timestamp in place, so the reported age keeps
/// growing until data is read again.
fn mark_refreshed() {
    let mut last = LAST_REFRESH.lock().expect("last refresh lock poisoned");
    *last = Some(Instant::now());
}

// ── Background refresh loop ───────────────────────────────────────────────────

/// Spawn a background task that refreshes the registry from PostgreSQL
/// every `interval`.
///
/// The task runs forever and is cancelled when the process exits.
/// Errors during refresh are logged but do not crash the task - the
/// registry continues serving the last known state.
///
/// IMPORTANT: The registry must be populated before this is called, either
/// via a one-shot `refresh_all_orgs()` in the caller or by waiting for the
/// schema to exist.  This loop is a refresh only: it expects the initial
/// load to have happened already.
pub fn spawn_refresh_loop(
    registry: TierRegistry,
    pool: sqlx::PgPool,
    interval: Duration,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(interval);
        ticker.tick().await; // skip the first immediate tick

        loop {
            ticker.tick().await;

            if let Err(e) = refresh_all_orgs(&registry, &pool).await {
                tracing::warn!(error = %e, "Registry refresh failed - keeping stale data");
            } else {
                let count = registry.total_count().await;
                info!(total_tools = count, "TierRegistry refreshed");
            }
        }
    })
}

/// Reload every active tool from `undolog_tool_registry` into the registry,
/// recording the refresh time on success.
pub(crate) async fn refresh_all_orgs(
    registry: &TierRegistry,
    pool: &sqlx::PgPool,
) -> Result<(), sqlx::Error> {
    use sqlx::Row;

    let rows = sqlx::query(
        r#"
        SELECT
            tool_id, org_id, tool_name, tool_version,
            tier::text,
            compensation_ref,
            irreversibility_reason,
            risk_tags,
            estimated_impact
        FROM undolog_tool_registry
        WHERE is_active = true
        "#,
    )
    .fetch_all(pool)
    .await?;

    // Group by org_id
    let mut by_org: HashMap<String, Vec<ToolRegistration>> = HashMap::new();

    for row in rows {
        let org_id_uuid: uuid::Uuid = row.try_get("org_id")?;
        let org_str = org_id_uuid.to_string();

        let tier_str: String = row.try_get("tier")?;
        let compensation_ref: Option<String> =
            row.try_get::<Option<String>, _>("compensation_ref")?;
        let irreversibility_reason: Option<String> =
            row.try_get::<Option<String>, _>("irreversibility_reason")?;
        let tier = tier_from_str(&tier_str, compensation_ref, irreversibility_reason);

        let tool_id_uuid: uuid::Uuid = row.try_get("tool_id")?;
        let risk_tags: Vec<String> = row.try_get("risk_tags").unwrap_or_default();

        let reg = ToolRegistration {
            tool_id: ToolId::from(tool_id_uuid),
            tool_name: row.try_get("tool_name")?,
            tool_version: row.try_get("tool_version")?,
            tier,
            risk_tags,
            estimated_impact: row.try_get("estimated_impact")?,
        };

        by_org.entry(org_str).or_default().push(reg);
    }

    for (org_str, regs) in by_org {
        let org_id_uuid = org_str
            .parse::<uuid::Uuid>()
            .expect("org_id from undolog_tool_registry.org_id must be a valid UUID");
        let org_id = OrgId::from(org_id_uuid);
        registry.replace_org(&org_id, regs).await;
    }

    mark_refreshed();
    Ok(())
}

/// Parse a tool tier string from the DB. Defaults to Safe for unknown values.
fn tier_from_str(
    s: &str,
    compensation_ref: Option<String>,
    irreversibility_reason: Option<String>,
) -> ToolTier {
    match s {
        "compensable" => {
            let fn_name =
                compensation_ref.unwrap_or_else(|| "__registry_compensable__".to_string());
            ToolTier::Compensable {
                compensation: undolog_types::CompensationDescriptor::new(
                    fn_name,
                    serde_json::Value::Null,
                ),
            }
        }
        "irreversible" => {
            let reason =
                irreversibility_reason.unwrap_or_else(|| "Registered as irreversible".to_string());
            ToolTier::Irreversible { reason }
        }
        _ => ToolTier::Safe,
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use undolog_types::ids::OrgId;

    fn make_reg(name: &str, tier: ToolTier) -> ToolRegistration {
        ToolRegistration {
            tool_id: ToolId::new(),
            tool_name: name.to_string(),
            tool_version: "1.0.0".to_string(),
            tier,
            risk_tags: vec![],
            estimated_impact: None,
        }
    }

    #[tokio::test]
    async fn upsert_and_get() {
        let reg = TierRegistry::new();
        let org = OrgId::new();
        reg.upsert(&org, make_reg("search_web", ToolTier::Safe)).await;

        let found = reg.get(&org, "search_web", "1.0.0").await;
        assert!(found.is_some());
        assert!(found.unwrap().tier.is_safe());
    }

    #[tokio::test]
    async fn unknown_tool_returns_none() {
        let reg = TierRegistry::new();
        let org = OrgId::new();
        assert!(reg.get(&org, "nonexistent", "1.0.0").await.is_none());
    }

    #[tokio::test]
    async fn orgs_are_isolated() {
        let reg = TierRegistry::new();
        let org_a = OrgId::new();
        let org_b = OrgId::new();
        reg.upsert(&org_a, make_reg("tool", ToolTier::Safe)).await;
        assert!(reg.get(&org_b, "tool", "1.0.0").await.is_none());
    }

    #[tokio::test]
    async fn replace_org_is_atomic() {
        let reg = TierRegistry::new();
        let org = OrgId::new();
        reg.upsert(&org, make_reg("old_tool", ToolTier::Safe)).await;

        let new_regs = vec![make_reg("new_tool", ToolTier::Safe)];
        reg.replace_org(&org, new_regs).await;

        assert!(reg.get(&org, "old_tool", "1.0.0").await.is_none());
        assert!(reg.get(&org, "new_tool", "1.0.0").await.is_some());
    }

    #[tokio::test]
    async fn total_count() {
        let reg = TierRegistry::new();
        let org = OrgId::new();
        assert_eq!(reg.total_count().await, 0);
        reg.upsert(&org, make_reg("a", ToolTier::Safe)).await;
        reg.upsert(&org, make_reg("b", ToolTier::Safe)).await;
        assert_eq!(reg.total_count().await, 2);
    }

    #[tokio::test]
    async fn registry_age_tracks_successful_refreshes_only() {
        // A freshly started engine has read nothing, so the age is unset.
        assert!(last_refresh_age_secs().is_none());

        // An unreachable database fails the refresh and keeps the age unset.
        // The one-second connect cap bounds the wait where the environment
        // drops connections instead of refusing them.
        let reg = TierRegistry::new();
        let pool = sqlx::postgres::PgPoolOptions::new()
            .connect_lazy("postgres://127.0.0.1:1/undolog_test?connect_timeout=1")
            .expect("a well-formed URL must build a lazy pool");
        refresh_all_orgs(&reg, &pool).await.expect_err("the refresh must fail");
        assert!(last_refresh_age_secs().is_none());

        // A successful refresh starts the clock, which then advances
        // without ever moving backwards across polls.
        mark_refreshed();
        let first = last_refresh_age_secs().expect("the age after the first refresh");
        assert!(first <= 1);
        tokio::time::sleep(Duration::from_millis(700)).await;
        let second = last_refresh_age_secs().expect("the age after another poll");
        tokio::time::sleep(Duration::from_millis(700)).await;
        let third = last_refresh_age_secs().expect("the age after a third poll");
        assert!(second >= first, "the age must not decrease: {first} -> {second}");
        assert!(third >= second, "the age must not decrease: {second} -> {third}");
        assert!(third >= 1, "the age must grow between polls");

        // A failed refresh keeps serving the age the last success recorded.
        refresh_all_orgs(&reg, &pool).await.expect_err("the refresh must fail");
        let after_failure = last_refresh_age_secs().expect("the age after the failure");
        assert!(
            after_failure >= third,
            "a failure must not reset the age: {third} -> {after_failure}"
        );

        // The next successful refresh resets the age below its last value.
        mark_refreshed();
        let reset = last_refresh_age_secs().expect("the age after the reset");
        assert!(reset < third, "a refresh must reset the age: {third} -> {reset}");
    }
}
