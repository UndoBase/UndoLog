//! undolog-store
//!
//! Storage layer for UndoLog. Supports PostgreSQL (default) and SQLite
//! (opt-in via `sqlite` feature). No SQL lives in undolog-engine or
//! undolog-saga.

pub mod approval_store;
pub mod effect_store;
pub mod retention;
pub mod session_store;

#[cfg(feature = "sqlite")]
pub mod sqlite;

pub use approval_store::ApprovalStore;
pub use effect_store::EffectStore;
pub use retention::RetentionStore;
pub use session_store::SessionStore;

use sqlx::PgPool;

/// Construct all three stores from a shared PostgreSQL connection pool.
/// Call once at application startup and share the stores via `Arc`.
pub fn build_stores(pool: PgPool) -> (EffectStore, SessionStore, ApprovalStore) {
    (EffectStore::new(pool.clone()), SessionStore::new(pool.clone()), ApprovalStore::new(pool))
}
