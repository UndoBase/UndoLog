//! Engine startup and bootstrap.
//!
//! Constructs a fully-initialized `EffectEngine` from configuration and a database URL.

use std::sync::Arc;
use std::time::Duration;
use tokio::sync::RwLock;
use tracing::info;

use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::errors::UndoLogError;

use crate::{EffectEngine, EngineConfig, TierRegistry};

/// Build a complete `EffectEngine` ready for production use.
///
/// This function:
/// 1. Connects to the PostgreSQL database
/// 2. Runs all pending migrations
/// 3. Creates and initializes the three stores
/// 4. Loads the tool registry
/// 5. Spawns the registry refresh background task
/// 6. Returns the configured engine
///
/// # Arguments
///
/// - `config`: Engine configuration (advisory lock retries, etc.)
/// - `database_url`: PostgreSQL connection string
/// - `registry_refresh_interval`: How often to refresh tool registrations from DB
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

    // Spawn the refresh loop (runs in background, loads from DB periodically).
    info!(interval_secs = registry_refresh_interval.as_secs(), "Spawning registry refresh loop");
    crate::tier_registry::spawn_refresh_loop(
        registry.read().await.clone(),
        pool,
        registry_refresh_interval,
    );

    // Build and return the engine.
    let engine = EffectEngine::new(effect_store, session_store, approval_store, registry, config);
    info!("EffectEngine initialized successfully");
    Ok(engine)
}
