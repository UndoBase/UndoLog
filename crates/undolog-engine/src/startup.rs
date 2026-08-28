//! Engine startup and bootstrap.
//!
//! Constructs a fully-initialized `EffectEngine` from configuration and a database URL.

use std::sync::Arc;
use std::time::Duration;
use tokio::sync::RwLock;
use tracing::{info, warn};

use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::errors::UndoLogError;

use crate::{EffectEngine, EngineConfig, TierRegistry};

/// Maximum time to wait for the database schema to be ready.
const SCHEMA_WAIT_RETRIES: u32 = 30;
const SCHEMA_WAIT_DELAY: Duration = Duration::from_secs(1);

/// Build a complete `EffectEngine` ready for production use.
///
/// This function:
/// 1. Connects to the PostgreSQL database
/// 2. Waits for core tables to exist (handles `docker-entrypoint-initdb.d`
///    race: those scripts run after Postgres accepts connections)
/// 3. Creates and initializes the three stores
/// 4. Loads the tool tier registry
/// 5. Spawns the registry refresh background task
/// 6. Returns the configured engine
///
/// # Arguments
///
/// - `config`: Engine configuration (advisory lock retries, etc.)
/// - `database_url`: PostgreSQL connection string
/// - `registry_refresh_interval`: How often to refresh tool registrations from DB
///
/// # Errors
///
/// Returns `UndoLogError::Internal` if the core schema tables do not appear
/// within the retry budget (30 s).  This prevents the engine from starting
/// with an empty registry, which would silently default every tool to SAFE.
///
/// # Example
///
/// ```ignore
/// let engine = build_engine(
///     EngineConfig::default(),
///     "postgresql://user:pass@localhost/undolog",
///     Duration::from_secs(60),
/// ).await?;
/// ```
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
    let timeout_interval = Duration::from_secs(config.timeout_check_interval_secs);
    info!(
        interval_secs = timeout_interval.as_secs(),
        approval_timeout_secs = config.approval_timeout_secs,
        auto_approve = config.auto_approve_on_timeout,
        "Spawning approval timeout processor"
    );
    spawn_timeout_processor(approval_store.clone(), timeout_interval);

    // Build and return the engine.
    let engine = EffectEngine::new(effect_store, session_store, approval_store, registry, config);
    info!("EffectEngine initialized successfully");
    Ok(engine)
}

/// Block until `undolog_tool_registry` exists in the public schema.
///
/// Polls `to_regclass` every second for up to 30 s.  The retry loop exists
/// because Docker Compose's `pg_isready` health check returns success before
/// `docker-entrypoint-initdb.d` scripts have finished.
///
/// Fails loudly on timeout so the operator knows migrations are missing,
/// rather than silently running with an empty registry.
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

/// Spawn a background task that periodically processes timed-out approvals.
///
/// The task iterates over all organisations with pending approvals and
/// calls `process_timeouts` to transition expired requests to `timed_out`
/// or `auto_approved` states. Each processed request gets an audit event
/// recorded in `undolog_approval_events`.
fn spawn_timeout_processor(approval_store: ApprovalStore, interval: Duration) {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

        loop {
            ticker.tick().await;

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
    });
}
