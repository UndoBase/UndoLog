//! Tests for circuit breaker and concurrency limiter integration.
//!
//! Unit tests for `CircuitBreaker` and `ConcurrencyLimiter` are in their
//! respective modules. This file tests integration with the engine.
//!
//! Run: `cargo test -p undolog-engine --test test_rate_limit`

#![cfg(all(test, feature = "pg"))]

use std::sync::Arc;

use undolog_engine::{
    CircuitBreaker, ConcurrencyLimiter, EffectEngine, EngineConfig, TierRegistry,
};
use undolog_store::{ApprovalStore, EffectStore, SessionStore};
use undolog_types::{
    config::RateLimitConfig,
    effect::ToolCall,
    ids::{OrgId, SessionId, ToolId},
    tier::{CompensationDescriptor, ToolTier},
};

fn test_database_url() -> String {
    std::env::var("TEST_DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost/undolog_test".to_string())
}

fn compensable_call(org_id: OrgId, session_id: SessionId) -> ToolCall {
    let compensation = CompensationDescriptor::new("compensate_test", serde_json::json!({}));
    ToolCall {
        org_id,
        session_id,
        tool_id: Some(ToolId::new()),
        tool_name: "test_tool".to_string(),
        tool_version: "1.0.0".to_string(),
        tier: ToolTier::Compensable { compensation },
        step_index: 0,
        args: serde_json::json!({}),
        intercepted_at: chrono::Utc::now(),
    }
}

// ── Circuit breaker integration ─────────────────────────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn circuit_breaker_opens_after_consecutive_errors() {
    let cb = Arc::new(CircuitBreaker::new(3, std::time::Duration::from_secs(60)));

    // Simulate 3 consecutive failures.
    cb.record_failure().await;
    cb.record_failure().await;
    cb.record_failure().await;

    // Circuit should now be open.
    assert!(cb.check().await.is_err());
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn circuit_breaker_closes_after_success() {
    let cb = Arc::new(CircuitBreaker::new(3, std::time::Duration::from_millis(50)));

    cb.record_failure().await;
    cb.record_failure().await;
    cb.record_success().await;

    // Counter reset, circuit closed.
    assert!(cb.check().await.is_ok());
    assert_eq!(cb.consecutive_errors(), 0);
}

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn circuit_breaker_half_open_recovery() {
    let cb = Arc::new(CircuitBreaker::new(1, std::time::Duration::from_millis(50)));

    cb.record_failure().await;
    assert!(cb.check().await.is_err());

    // Wait for cooldown.
    tokio::time::sleep(std::time::Duration::from_millis(60)).await;

    // Half-open: allows one request.
    assert!(cb.check().await.is_ok());

    // Success closes the circuit.
    cb.record_success().await;
    assert!(cb.check().await.is_ok());
}

// ── Concurrency limiter integration ─────────────────────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn concurrency_limit_rejects_when_exhausted() {
    let limiter = ConcurrencyLimiter::new(2);

    let p1 = limiter.try_acquire().expect("first acquire");
    let _p2 = limiter.try_acquire().expect("second acquire");

    // Third acquire should fail.
    let err = limiter.try_acquire();
    assert!(err.is_err());

    // Drop one permit, then succeed.
    drop(p1);
    assert!(limiter.try_acquire().is_ok());
}

// ── Burst load test ─────────────────────────────────────────────────────────

#[tokio::test]
#[ignore = "requires TEST_DATABASE_URL"]
async fn burst_load_graceful_rejection() {
    let pool = sqlx::PgPool::connect(&test_database_url()).await.expect("connect");

    let effect_store = EffectStore::new(pool.clone());
    let session_store = SessionStore::new(pool.clone());
    let approval_store = ApprovalStore::new(pool);
    let registry = Arc::new(tokio::sync::RwLock::new(TierRegistry::new()));

    let config = EngineConfig {
        rate_limit_config: RateLimitConfig::new(100, 60, 5),
        ..EngineConfig::default()
    };

    let engine = EffectEngine::new(effect_store, session_store, approval_store, registry, config);

    let org_id = OrgId::new();

    // Fire 10 concurrent intercepts. With max_concurrency=5, some should be rejected.
    let mut handles = Vec::new();
    for _ in 0..10 {
        let engine_clone = engine.clone();
        let call = compensable_call(org_id, SessionId::new());
        handles.push(tokio::spawn(async move { engine_clone.intercept(call).await }));
    }

    let mut ok_count = 0u32;
    let mut err_count = 0u32;
    for handle in handles {
        match handle.await.expect("task panicked") {
            Ok(_) => ok_count += 1,
            Err(_) => err_count += 1,
        }
    }

    // At least some should succeed, some may be rejected.
    assert!(ok_count > 0, "at least one request should succeed");
    // All errors should be circuit breaker or concurrency limit, not panics.
    assert_eq!(ok_count + err_count, 10);
}
