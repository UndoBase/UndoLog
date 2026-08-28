//! UndoLog Engine gRPC Server
//!
//! Starts the tonic gRPC server for tool call interception, commit, fail,
//! approve, and reject operations - plus an HTTP health endpoint for
//! orchestration liveness checks.

use std::net::SocketAddr;
use std::time::Duration;

use anyhow::{Context, Result};
use tokio::net::TcpListener;
use tokio::signal;
use tonic::transport::Server;
use tracing::{info, warn};

use undolog_engine::grpc::pb::undo_log_engine_server::UndoLogEngineServer;
use undolog_engine::grpc::UndoLogEngineService;
use undolog_engine::startup;
use undolog_engine::EngineConfig;

// ── Env var defaults ────────────────────────────────────────────────────────

const ENV_DATABASE_URL: &str = "DATABASE_URL";
const ENV_GRPC_ADDR: &str = "UNDOLOG_ENGINE_GRPC_ADDR";
const ENV_HEALTH_ADDR: &str = "UNDOLOG_ENGINE_HEALTH_ADDR";
const ENV_LOG_LEVEL: &str = "UNDOLOG_LOG_LEVEL";
const ENV_REGISTRY_REFRESH_SECS: &str = "UNDOLOG_REGISTRY_REFRESH_SECS";
const ENV_LOCK_MAX_ATTEMPTS: &str = "UNDOLOG_LOCK_MAX_ATTEMPTS";
const ENV_LOCK_RETRY_MS: &str = "UNDOLOG_LOCK_RETRY_MS";
const ENV_APPROVAL_TIMEOUT_SECS: &str = "UNDOLOG_APPROVAL_TIMEOUT_SECS";
const ENV_AUTO_APPROVE_ON_TIMEOUT: &str = "UNDOLOG_AUTO_APPROVE_ON_TIMEOUT";
const ENV_TIMEOUT_CHECK_INTERVAL_SECS: &str = "UNDOLOG_TIMEOUT_CHECK_INTERVAL_SECS";

const DEFAULT_GRPC_ADDR: &str = "0.0.0.0:50051";
const DEFAULT_HEALTH_ADDR: &str = "0.0.0.0:9090";
const DEFAULT_LOG_LEVEL: &str = "info";
const DEFAULT_REGISTRY_REFRESH_SECS: &str = "15";
const DEFAULT_APPROVAL_TIMEOUT_SECS: &str = "86400";
const DEFAULT_TIMEOUT_CHECK_INTERVAL_SECS: &str = "60";

// ── Entry point ─────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> Result<()> {
    // Parse config from environment.
    let log_level = env_or(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL);
    let database_url =
        env_or(ENV_DATABASE_URL, "postgresql://postgres:postgres@localhost:5432/undolog_dev");
    let grpc_addr: SocketAddr = env_or(ENV_GRPC_ADDR, DEFAULT_GRPC_ADDR)
        .parse()
        .context("Invalid UNDOLOG_ENGINE_GRPC_ADDR - expected ip:port")?;
    let health_addr: SocketAddr = env_or(ENV_HEALTH_ADDR, DEFAULT_HEALTH_ADDR)
        .parse()
        .context("Invalid UNDOLOG_ENGINE_HEALTH_ADDR - expected ip:port")?;
    let registry_refresh_secs: u64 =
        env_or(ENV_REGISTRY_REFRESH_SECS, DEFAULT_REGISTRY_REFRESH_SECS)
            .parse()
            .context("Invalid UNDOLOG_REGISTRY_REFRESH_SECS")?;
    let lock_max_attempts: u32 =
        env_or(ENV_LOCK_MAX_ATTEMPTS, "3").parse().context("Invalid UNDOLOG_LOCK_MAX_ATTEMPTS")?;
    let lock_retry_ms: u64 =
        env_or(ENV_LOCK_RETRY_MS, "100").parse().context("Invalid UNDOLOG_LOCK_RETRY_MS")?;
    let approval_timeout_secs: i64 =
        env_or(ENV_APPROVAL_TIMEOUT_SECS, DEFAULT_APPROVAL_TIMEOUT_SECS)
            .parse()
            .context("Invalid UNDOLOG_APPROVAL_TIMEOUT_SECS")?;
    let auto_approve_on_timeout: bool = env_or(ENV_AUTO_APPROVE_ON_TIMEOUT, "false")
        .parse()
        .context("Invalid UNDOLOG_AUTO_APPROVE_ON_TIMEOUT")?;
    let timeout_check_interval_secs: u64 =
        env_or(ENV_TIMEOUT_CHECK_INTERVAL_SECS, DEFAULT_TIMEOUT_CHECK_INTERVAL_SECS)
            .parse()
            .context("Invalid UNDOLOG_TIMEOUT_CHECK_INTERVAL_SECS")?;

    // Initialize structured JSON logging.
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::builder()
                .with_default_directive(log_level.parse().unwrap_or(tracing::Level::INFO.into()))
                .from_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new(&log_level)),
        )
        .json()
        .init();

    let engine_config = EngineConfig {
        lock_max_attempts,
        lock_retry_ms,
        approval_timeout_secs,
        auto_approve_on_timeout,
        timeout_check_interval_secs,
    };

    info!(
        grpc_addr = %grpc_addr,
        health_addr = %health_addr,
        "Starting UndoLog Engine gRPC server"
    );

    // Bootstrap the EffectEngine - connects to Postgres, initialises stores,
    // loads the tier registry, and spawns the registry refresh loop.
    let engine = startup::build_engine(
        engine_config,
        &database_url,
        Duration::from_secs(registry_refresh_secs),
    )
    .await
    .context("Failed to bootstrap EffectEngine")?;

    // Wrap engine in the tonic gRPC service.
    let grpc_service = UndoLogEngineServer::new(UndoLogEngineService::new(engine));

    // ── Start health HTTP endpoint on a background task ───────────────────
    let health_handle = tokio::spawn(health_server(health_addr));

    // ── Start gRPC server ────────────────────────────────────────────────
    info!(addr = %grpc_addr, "gRPC server listening");

    Server::builder()
        .add_service(grpc_service)
        .serve_with_shutdown(grpc_addr, shutdown_signal())
        .await
        .context("gRPC server terminated with error")?;

    // Graceful shutdown: abort the health endpoint.
    health_handle.abort();
    info!("UndoLog Engine stopped");
    Ok(())
}

// ── Health endpoint ─────────────────────────────────────────────────────────

/// Minimal HTTP health server.
///
/// Responds `200 OK` with a JSON body on every request.  No routing - every
/// path returns healthy.  Designed for orchestration liveness probes.
async fn health_server(addr: SocketAddr) {
    let listener = TcpListener::bind(addr).await.expect("Failed to bind health endpoint");
    info!(addr = %addr, "Health endpoint ready");

    loop {
        let (mut stream, peer) = match listener.accept().await {
            Ok(conn) => conn,
            Err(e) => {
                warn!(error = %e, "Health accept failed");
                continue;
            }
        };

        tokio::spawn(async move {
            use tokio::io::AsyncWriteExt;

            let body = b"{\"status\":\"ok\",\"service\":\"undolog-engine\"}\n";
            let response = format!(
                "HTTP/1.1 200 OK\r\n\
                 content-type: application/json\r\n\
                 content-length: {}\r\n\
                 connection: close\r\n\r\n",
                body.len()
            );
            if let Err(e) = stream.write_all(response.as_bytes()).await {
                warn!(peer = %peer, error = %e, "Health write failed");
                return;
            }
            if let Err(e) = stream.write_all(body).await {
                warn!(peer = %peer, error = %e, "Health body write failed");
            }
        });
    }
}

// ── Shutdown signal ─────────────────────────────────────────────────────────

/// Waits for SIGINT (Ctrl+C) or SIGTERM, then returns so the gRPC server
/// can perform a graceful shutdown.
async fn shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c().await.expect("failed to install SIGINT handler");
    };

    #[cfg(unix)]
    let term = {
        let mut sig = signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler");
        async move { sig.recv().await }
    };

    #[cfg(not(unix))]
    let term = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => info!("Caught SIGINT"),
        _ = term   => info!("Caught SIGTERM"),
    }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Read an env var or return the default.
fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}
