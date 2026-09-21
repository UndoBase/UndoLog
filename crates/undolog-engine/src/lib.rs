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
//!   - [`timeout`]        - approval timeout background processor
//!   - [`admin`]          - HTTP admin API for dead-letter management
//!   - [`SessionCache`]   - in-memory session state cache
//!   - [`circuit_breaker`] - circuit breaker for cascading failure protection
//!   - [`rate_limit`]     - concurrency limiter for backpressure

pub mod admin;
pub mod cache;
pub mod circuit_breaker;
pub mod engine;
pub mod grpc;
pub mod rate_limit;
pub mod startup;
pub mod telemetry;
pub mod tier_registry;
pub mod timeout;

pub use cache::SessionCache;
pub use circuit_breaker::{CircuitBreaker, CircuitState};
pub use engine::{EffectEngine, EngineConfig, InterceptOutcome};
pub use rate_limit::ConcurrencyLimiter;
pub use tier_registry::{TierRegistry, ToolRegistration};
