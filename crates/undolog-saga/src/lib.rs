//! UndoLog saga orchestration.
//!
//! This crate drives crash-safe compensation over the persisted undo stack.

#![allow(missing_docs)]
//! It coordinates the storage layer and the compensation registry so callers
//! only need a single entry point to roll back a failed session.

pub mod compensation_runner;
pub mod orchestrator;

pub use orchestrator::{
    CompensationRegistry, DatabaseSagaStore, RetryConfig, SagaError, SagaOrchestrator, SagaStore,
};
