//! undolog-store
//!
//! All PostgreSQL access for UndoLog is centralised here.
//! No SQL lives in undolog-engine or undolog-saga.

pub mod approval_store;
pub mod effect_store;
pub mod session_store;

pub use approval_store::ApprovalStore;
pub use effect_store::EffectStore;
pub use session_store::SessionStore;

use sqlx::PgPool;

/// Construct all three stores from a shared connection pool.
/// Call once at application startup and share the stores via `Arc`.
pub fn build_stores(pool: PgPool) -> (EffectStore, SessionStore, ApprovalStore) {
    (EffectStore::new(pool.clone()), SessionStore::new(pool.clone()), ApprovalStore::new(pool))
}
