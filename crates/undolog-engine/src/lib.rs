//! undolog-engine
//!
//! The UndoLog Effect Engine - safety kernel of the UndoLog runtime.
//!
//! Public surface:
//!   - [`EffectEngine`]   - core tool call interception and routing
//!   - [`EngineConfig`]   - engine configuration
//!   - [`InterceptOutcome`] - routing decision enum
//!   - [`TierRegistry`]   - in-memory tool tier cache
//!   - [`startup`]        - pool construction + initial bootstrap

pub mod engine;
pub mod grpc;
pub mod startup;
pub mod tier_registry;

pub use engine::{EffectEngine, EngineConfig, InterceptOutcome};
pub use tier_registry::{TierRegistry, ToolRegistration};
