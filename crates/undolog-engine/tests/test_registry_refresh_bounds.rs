//! Startup bounds for `UNDOLOG_REGISTRY_REFRESH_SECS`.
//!
//! Each test spawns the engine binary and checks that the refresh interval
//! is validated before anything touches the database: rejected values exit
//! with an error naming the variable and the minimum, while accepted values
//! reach database bootstrap, which fails on URL parsing so no server or
//! network is needed.

#![cfg(feature = "pg")]

use std::process::Stdio;
use std::time::Duration;

use tokio::process::Command;

/// A database URL that fails on parsing, so an accepted configuration
/// stops at bootstrap instantly on every machine. An unreachable address
/// would depend on how fast the environment refuses the connection, while
/// sqlx allows a connect attempt up to 30 seconds.
const MALFORMED_DATABASE_URL: &str = "not-a-database-url";

/// Run the engine binary with the given refresh interval override and
/// return its standard error once it exits.
async fn run_engine(refresh_secs: Option<&str>) -> String {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_undolog-engine"));
    cmd.env("DATABASE_URL", MALFORMED_DATABASE_URL);
    // Pin the remaining engine configuration to its defaults, so a
    // developer's exported settings cannot change which error is hit.
    for key in [
        "UNDOLOG_ENGINE_GRPC_ADDR",
        "UNDOLOG_ENGINE_HEALTH_ADDR",
        "UNDOLOG_LOCK_MAX_ATTEMPTS",
        "UNDOLOG_LOCK_RETRY_MS",
        "UNDOLOG_APPROVAL_TIMEOUT_SECS",
        "UNDOLOG_AUTO_APPROVE_ON_TIMEOUT",
        "UNDOLOG_TIMEOUT_CHECK_INTERVAL_SECS",
        "UNDOLOG_ENGINE_AUTH_TOKEN",
        "UNDOLOG_LOG_LEVEL",
    ] {
        cmd.env_remove(key);
    }
    match refresh_secs {
        Some(secs) => {
            cmd.env("UNDOLOG_REGISTRY_REFRESH_SECS", secs);
        }
        None => {
            cmd.env_remove("UNDOLOG_REGISTRY_REFRESH_SECS");
        }
    }
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    let output = tokio::time::timeout(Duration::from_secs(20), cmd.output())
        .await
        .expect("the engine must exit instead of hanging")
        .expect("the engine process must start");
    assert!(!output.status.success(), "the engine must stop when its database is unreachable");
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[tokio::test]
async fn refresh_interval_below_the_minimum_is_rejected() {
    for value in ["0", "4"] {
        let stderr = run_engine(Some(value)).await;
        assert!(
            stderr.contains("Invalid UNDOLOG_REGISTRY_REFRESH_SECS"),
            "value {value} must be rejected by name, stderr:\n{stderr}"
        );
        assert!(
            stderr.contains("at least 5 seconds"),
            "value {value} must name the minimum, stderr:\n{stderr}"
        );
    }
}

#[tokio::test]
async fn refresh_interval_at_the_minimum_and_the_default_is_accepted() {
    for value in [Some("5"), None] {
        let label = value.unwrap_or("the default");
        let stderr = run_engine(value).await;
        assert!(
            !stderr.contains("Invalid UNDOLOG_REGISTRY_REFRESH_SECS"),
            "{label} must pass validation, stderr:\n{stderr}"
        );
        assert!(
            stderr.contains("Failed to bootstrap EffectEngine"),
            "{label} must reach database bootstrap, stderr:\n{stderr}"
        );
    }
}
