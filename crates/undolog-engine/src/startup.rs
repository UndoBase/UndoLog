//! Engine startup and bootstrap.
//!
//! Constructs a fully-initialized `EffectEngine` from configuration and a database URL.

use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::info;

#[cfg(feature = "pg")]
use std::time::Duration;
#[cfg(feature = "pg")]
use tracing::warn;

#[cfg(feature = "pg")]
use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::errors::UndoLogError;

use crate::{EffectEngine, EngineConfig, TierRegistry};

/// Maximum time to wait for the database schema to be ready.
#[cfg(feature = "pg")]
const SCHEMA_WAIT_RETRIES: u32 = 30;
#[cfg(feature = "pg")]
const SCHEMA_WAIT_DELAY: Duration = Duration::from_secs(1);

/// Build a complete `EffectEngine` ready for production use (PostgreSQL).
#[cfg(feature = "pg")]
pub async fn build_engine(
    config: EngineConfig,
    database_url: &str,
    registry_refresh_interval: Duration,
) -> Result<EffectEngine, UndoLogError> {
    // Connect to PostgreSQL.
    info!("Connecting to database: {}", database_url);
    let pool = sqlx::PgPool::connect(database_url).await?;

    // Wait for core tables to exist.  In Docker Compose environments the
    // `docker-entrypoint-initdb.d` scripts run asynchronously after Postgres
    // accepts connections: if we try to query the registry before the
    // schema exists we silently default every tool to SAFE.
    //
    // This is a startup-only barrier: once past it, the schema is guaranteed
    // to exist.  The seed data may still be loading (rows in `undolog_orgs`
    // and `undolog_tool_registry`), but `refresh_all_orgs` handles 0-row
    // results gracefully: the background refresh loop catches up within 60s.
    wait_for_schema(&pool).await?;

    // Run pending migrations.
    // Note: Migrations are managed at the workspace root level and should be run
    // separately before creating the engine. For now, we skip them here to avoid
    // sqlx macro path resolution issues.
    info!("Skipping migrations (managed separately)");
    // sqlx::migrate!("../migrations").run(&pool).await?;

    // Create the three concrete stores.
    let effect_store = EffectStore::new(pool.clone());
    let session_store = SessionStore::new(pool.clone());
    let approval_store = ApprovalStore::new(pool.clone());

    // Create the tier registry (initially empty).
    let registry = TierRegistry::new();

    // Wrap registry in Arc<RwLock> for concurrent access.
    let registry = Arc::new(RwLock::new(registry));

    // Load tools from the DB so the registry is populated before the engine
    // handles its first request.  The background loop keeps it fresh.
    //
    // Retry with back-off because seed-data migrations (0003, 0004) may not
    // have finished yet when the table first appears.  Without this retry the
    // engine can start with an empty registry, defaulting every tool to SAFE
    // and silently disabling replay detection for the first 60 s.
    for attempt in 0u32..SCHEMA_WAIT_RETRIES {
        match crate::tier_registry::refresh_all_orgs(&*registry.read().await, &pool).await {
            Err(e) => {
                tracing::warn!(attempt, error = %e, "Registry load failed, retrying");
            }
            Ok(()) => {
                let count = registry.read().await.total_count().await;
                if count > 0 {
                    info!(total_tools = count, "TierRegistry loaded from database");
                    break;
                }
                tracing::warn!(
                    attempt,
                    "No tool registrations found (seed data may not be applied yet)"
                );
            }
        }
        if attempt + 1 < SCHEMA_WAIT_RETRIES {
            tokio::time::sleep(SCHEMA_WAIT_DELAY).await;
        }
    }

    // Spawn the refresh loop (runs in background, re-loads from DB periodically).
    info!(interval_secs = registry_refresh_interval.as_secs(), "Spawning registry refresh loop");
    crate::tier_registry::spawn_refresh_loop(
        registry.read().await.clone(),
        pool.clone(),
        registry_refresh_interval,
    );

    // Spawn the approval timeout processor (runs in background, processes timed-out approvals).
    let timeout_config = config.approval_timeout_config();
    info!(
        interval_secs = timeout_config.check_interval_secs,
        approval_timeout_secs = timeout_config.timeout_secs,
        auto_approve = timeout_config.auto_approve,
        "Spawning approval timeout processor"
    );
    crate::timeout::spawn_timeout_processor(approval_store.clone(), &timeout_config);

    // Build and return the engine.
    let engine = EffectEngine::new(effect_store, session_store, approval_store, registry, config);
    info!("EffectEngine initialized successfully");
    Ok(engine)
}

/// Block until `undolog_tool_registry` exists in the public schema (PostgreSQL).
#[cfg(feature = "pg")]
async fn wait_for_schema(pool: &sqlx::PgPool) -> Result<(), UndoLogError> {
    for attempt in 0u32..SCHEMA_WAIT_RETRIES {
        let ready: bool =
            sqlx::query_scalar("SELECT to_regclass('public.undolog_tool_registry') IS NOT NULL")
                .fetch_one(pool)
                .await
                .unwrap_or(false);

        if ready {
            info!(attempt, "Database schema ready");
            return Ok(());
        }

        if attempt + 1 < SCHEMA_WAIT_RETRIES {
            warn!(
                attempt,
                retry_delay_ms = SCHEMA_WAIT_DELAY.as_millis(),
                "Schema not ready, waiting"
            );
            tokio::time::sleep(SCHEMA_WAIT_DELAY).await;
        }
    }

    Err(UndoLogError::Internal(
        "Core table undolog_tool_registry not found after 30s: \
         ensure database migrations have been applied"
            .into(),
    ))
}

// ── SQLite startup ──────────────────────────────────────────────────────────

/// Build a complete `EffectEngine` backed by SQLite (local development).
///
/// Opens (or creates) the SQLite database at the given path, initializes the
/// schema, and constructs the three stores. No advisory locks, no RLS, no
/// partitioning. Suitable for local development and testing only.
#[cfg(all(feature = "sqlite", not(feature = "pg")))]
pub async fn build_engine_sqlite(
    config: EngineConfig,
    database_path: &str,
) -> Result<EffectEngine, UndoLogError> {
    info!(path = %database_path, "Initializing SQLite storage adapter");

    let (effect_store, session_store, approval_store) =
        undolog_store::sqlite::build_stores(database_path).await?;

    let registry = TierRegistry::new();
    let registry = Arc::new(RwLock::new(registry));

    let timeout_config = config.approval_timeout_config();
    crate::timeout::spawn_timeout_processor(approval_store.clone(), &timeout_config);

    let engine = EffectEngine::new(effect_store, session_store, approval_store, registry, config);
    info!("EffectEngine initialized (SQLite backend)");
    Ok(engine)
}
