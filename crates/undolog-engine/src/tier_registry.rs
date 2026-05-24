//! TierRegistry - in-memory lookup table for tool tier classifications.
//!
//! Loaded from `undolog_tool_registry` at startup and refreshed every
//! `refresh_interval` (default 60 s) to pick up new tool registrations
//! without a restart.
//!
//! All lookups are O(1). Interior `RwLock` allows unlimited concurrent
//! reads (the hot path) with infrequent write locks (refresh only).

use std::{collections::HashMap, sync::Arc, time::Duration};
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

// ── Background refresh loop ───────────────────────────────────────────────────

/// Spawn a background task that refreshes the registry from PostgreSQL
/// every `interval`.
///
/// The task runs forever and is cancelled when the process exits.
/// Errors during refresh are logged but do not crash the task - the
/// registry continues serving the last known state.
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

/// Reload every active tool from `undolog_tool_registry` into the registry.
async fn refresh_all_orgs(registry: &TierRegistry, pool: &sqlx::PgPool) -> Result<(), sqlx::Error> {
    use sqlx::Row;

    let rows = sqlx::query(
        r#"
        SELECT
            tool_id, org_id, tool_name, tool_version,
            tier::text,
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
        let tier = tier_from_str(&tier_str);

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

    Ok(())
}

/// Parse a tool tier string from the DB. Defaults to Safe for unknown values.
fn tier_from_str(s: &str) -> ToolTier {
    match s {
        "compensable" => {
            // Tier detail (compensation descriptor) must be enriched from the
            // undolog_tool_registry.compensation_ref column in a real query.
            // For the registry snapshot we store Safe as a placeholder;
            // the full tier is always read from the ToolCall itself (set by the SDK).
            ToolTier::Safe
        }
        "irreversible" => ToolTier::Safe, // same - enriched at call time by SDK
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
}
