//! SQLite storage adapter for UndoLog.
//!
//! Provides local development and testing support without requiring PostgreSQL.
//!
//! Limitations compared to the PostgreSQL backend:
//! - No advisory locks (uses SQLite WAL mode instead)
//! - No row-level security (RLS)
//! - No partitioning
//! - No trigger-based session counter updates

mod approval_store;
mod effect_store;
mod schema;
mod session_store;
mod util;

pub use approval_store::ApprovalStore;
pub use effect_store::EffectStore;
pub use session_store::SessionStore;

use sqlx::sqlite::SqlitePoolOptions;
use tracing::info;
use undolog_types::errors::UndoLogError;

/// Construct all three stores from a SQLite database path.
///
/// Creates the database file if it does not exist, initializes the schema,
/// and returns a connection pool. Suitable for local development.
pub async fn build_stores(
    database_path: &str,
) -> Result<(EffectStore, SessionStore, ApprovalStore), UndoLogError> {
    let url = format!("sqlite:{database_path}?mode=rwc");
    let pool = SqlitePoolOptions::new()
        .max_connections(1)
        .connect(&url)
        .await
        .map_err(|e| UndoLogError::Internal(format!("Failed to open SQLite database: {e}")))?;

    schema::initialize(&pool).await?;

    info!(path = %database_path, "SQLite storage adapter initialized");

    Ok((EffectStore::new(pool.clone()), SessionStore::new(pool.clone()), ApprovalStore::new(pool)))
}
