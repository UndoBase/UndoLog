//! Transport TLS and mutual TLS (mTLS) tests for the engine gRPC service.

use std::net::{SocketAddr, TcpListener as StdTcpListener};
use std::path::PathBuf;
use std::time::Duration;

use tokio::sync::oneshot;
use tonic::transport::{Certificate, Channel, ClientTlsConfig, Identity, Server, ServerTlsConfig};
use tonic::{Request, Response, Status};

use undolog_engine::config::{
    TlsConfig, TlsConfigError, ENV_TLS_CERT_PATH, ENV_TLS_CLIENT_CA_PATH, ENV_TLS_KEY_PATH,
};
use undolog_engine::grpc::pb;
use undolog_engine::grpc::pb::undo_log_engine_client::UndoLogEngineClient;
use undolog_engine::grpc::pb::undo_log_engine_server::{UndoLogEngine, UndoLogEngineServer};
use undolog_engine::startup;

// ── Stub engine ────────────────────────────────────────────────────────────

/// Minimal stub engine for transport security testing.
struct StubEngine;

#[tonic::async_trait]
impl UndoLogEngine for StubEngine {
    async fn intercept(
        &self,
        _request: Request<pb::InterceptRequest>,
    ) -> Result<Response<pb::InterceptResponse>, Status> {
        Ok(Response::new(pb::InterceptResponse { outcome: None, error: "stub".to_string() }))
    }

    async fn commit(
        &self,
        _request: Request<pb::CommitRequest>,
    ) -> Result<Response<pb::CommitResponse>, Status> {
        Ok(Response::new(pb::CommitResponse {}))
    }

    async fn fail(
        &self,
        _request: Request<pb::FailRequest>,
    ) -> Result<Response<pb::FailResponse>, Status> {
        Ok(Response::new(pb::FailResponse {}))
    }

    async fn approve(
        &self,
        _request: Request<pb::ApproveRequest>,
    ) -> Result<Response<pb::ApproveResponse>, Status> {
        Ok(Response::new(pb::ApproveResponse {
            effect_id: "stub-effect".to_string(),
            session_id: "stub-session".to_string(),
            tool_name: "stub-tool".to_string(),
            tool_version: "1".to_string(),
            args: b"{}".to_vec(),
        }))
    }

    async fn reject(
        &self,
        _request: Request<pb::RejectRequest>,
    ) -> Result<Response<pb::RejectResponse>, Status> {
        Ok(Response::new(pb::RejectResponse {}))
    }

    async fn list_pending_approvals(
        &self,
        _request: Request<pb::ListPendingApprovalsRequest>,
    ) -> Result<Response<pb::ListPendingApprovalsResponse>, Status> {
        Ok(Response::new(pb::ListPendingApprovalsResponse { approval_records: Vec::new() }))
    }

    async fn get_approval(
        &self,
        _request: Request<pb::GetApprovalRequest>,
    ) -> Result<Response<pb::GetApprovalResponse>, Status> {
        Ok(Response::new(pb::GetApprovalResponse { record: None }))
    }

    async fn list_dead_letters(
        &self,
        _request: Request<pb::ListDeadLettersRequest>,
    ) -> Result<Response<pb::ListDeadLettersResponse>, Status> {
        Ok(Response::new(pb::ListDeadLettersResponse { records: Vec::new(), count: 0 }))
    }

    async fn retry_dead_letter(
        &self,
        _request: Request<pb::RetryDeadLetterRequest>,
    ) -> Result<Response<pb::RetryDeadLetterResponse>, Status> {
        Ok(Response::new(pb::RetryDeadLetterResponse { record: None }))
    }

    async fn skip_dead_letter(
        &self,
        _request: Request<pb::SkipDeadLetterRequest>,
    ) -> Result<Response<pb::SkipDeadLetterResponse>, Status> {
        Ok(Response::new(pb::SkipDeadLetterResponse { record: None }))
    }
}

// ── Test server harness ────────────────────────────────────────────────────

struct TlsServer {
    addr: SocketAddr,
    shutdown: oneshot::Sender<()>,
    task: tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
}

impl TlsServer {
    async fn stop(self) {
        let _ = self.shutdown.send(());
        let result = self.task.await.expect("server task must not panic");
        assert!(result.is_ok(), "server must shut down cleanly: {result:?}");
    }
}

async fn start_server(tls: Option<ServerTlsConfig>) -> TlsServer {
    let probe = StdTcpListener::bind("127.0.0.1:0").expect("probe ephemeral loopback port");
    let addr: SocketAddr = probe.local_addr().expect("read ephemeral address");
    drop(probe);

    let (shutdown_tx, shutdown_rx) = oneshot::channel::<()>();
    let service = UndoLogEngineServer::new(StubEngine);

    let task = tokio::spawn(async move {
        let mut builder = Server::builder();
        if let Some(tls_cfg) = tls {
            builder = builder.tls_config(tls_cfg).expect("configure server TLS");
        }
        builder
            .add_service(service)
            .serve_with_shutdown(addr, async move {
                let _ = shutdown_rx.await;
            })
            .await
    });

    TlsServer { addr, shutdown: shutdown_tx, task }
}

// ── Fixture paths ──────────────────────────────────────────────────────────

fn fixture_path(filename: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(filename)
}

fn read_fixture(filename: &str) -> Vec<u8> {
    std::fs::read(fixture_path(filename)).expect("read fixture file")
}

// ── Connect helpers ────────────────────────────────────────────────────────

async fn connect_plaintext(
    addr: SocketAddr,
) -> Result<UndoLogEngineClient<Channel>, tonic::transport::Error> {
    let url = format!("http://{addr}");
    for _ in 0..50 {
        if let Ok(client) = UndoLogEngineClient::connect(url.clone()).await {
            return Ok(client);
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    UndoLogEngineClient::connect(url).await
}

async fn connect_tls(
    addr: SocketAddr,
    ca_pem: Vec<u8>,
    client_identity: Option<(Vec<u8>, Vec<u8>)>,
) -> Result<UndoLogEngineClient<Channel>, tonic::transport::Error> {
    let ca_cert = Certificate::from_pem(ca_pem);
    let mut tls = ClientTlsConfig::new().ca_certificate(ca_cert).domain_name("localhost");

    if let Some((cert, key)) = client_identity {
        let identity = Identity::from_pem(cert, key);
        tls = tls.identity(identity);
    }

    let channel = Channel::from_shared(format!("https://{addr}"))
        .expect("valid uri")
        .tls_config(tls)?
        .connect_timeout(Duration::from_secs(3))
        .timeout(Duration::from_secs(3));

    for _ in 0..50 {
        if let Ok(conn) = channel.connect().await {
            return Ok(UndoLogEngineClient::new(conn));
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }

    let conn = channel.connect().await?;
    Ok(UndoLogEngineClient::new(conn))
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[tokio::test]
async fn test_plaintext_startup_and_handshake() {
    let server = start_server(None).await;
    let mut client =
        connect_plaintext(server.addr).await.expect("plaintext client connects successfully");

    let resp = client.commit(Request::new(pb::CommitRequest::default())).await;

    assert!(resp.is_ok(), "commit RPC succeeds over plaintext: {resp:?}");
    server.stop().await;
}

#[tokio::test]
async fn test_server_tls_handshake_succeeds() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("server.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: None,
    };
    let server_tls = startup::build_server_tls_config(Some(&tls_config))
        .expect("build server TLS config")
        .expect("must be Some");

    let server = start_server(Some(server_tls)).await;
    let ca_pem = read_fixture("ca.crt");

    let mut client = connect_tls(server.addr, ca_pem, None)
        .await
        .expect("TLS client connects successfully with trusted CA");

    let resp = client.commit(Request::new(pb::CommitRequest::default())).await;

    assert!(resp.is_ok(), "commit RPC succeeds over TLS: {resp:?}");
    server.stop().await;
}

#[tokio::test]
async fn test_mtls_handshake_succeeds_with_trusted_client_cert() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("server.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: Some(fixture_path("ca.crt")),
    };
    let server_tls = startup::build_server_tls_config(Some(&tls_config))
        .expect("build mTLS config")
        .expect("must be Some");

    let server = start_server(Some(server_tls)).await;
    let ca_pem = read_fixture("ca.crt");
    let client_cert = read_fixture("client.crt");
    let client_key = read_fixture("client.key");

    let mut client = connect_tls(server.addr, ca_pem, Some((client_cert, client_key)))
        .await
        .expect("mTLS client connects successfully with trusted client certificate");

    let resp = client.commit(Request::new(pb::CommitRequest::default())).await;

    assert!(resp.is_ok(), "commit RPC succeeds over mTLS: {resp:?}");
    server.stop().await;
}

#[tokio::test]
async fn test_mtls_rejected_when_client_cert_missing() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("server.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: Some(fixture_path("ca.crt")),
    };
    let server_tls = startup::build_server_tls_config(Some(&tls_config))
        .expect("build mTLS config")
        .expect("must be Some");

    let server = start_server(Some(server_tls)).await;
    let ca_pem = read_fixture("ca.crt");

    // Client connects with trusted CA but presents NO client identity.
    let client_result = connect_tls(server.addr, ca_pem, None).await;
    match client_result {
        Err(_) => {} // Connection handshake failed as expected.
        Ok(mut client) => {
            // If connection was established before handshake completion, RPC must fail.
            let rpc_result = client.commit(Request::new(pb::CommitRequest::default())).await;
            assert!(rpc_result.is_err(), "mTLS server must reject RPC without client certificate");
        }
    }

    server.stop().await;
}

#[tokio::test]
async fn test_mtls_rejected_when_client_cert_untrusted() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("server.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: Some(fixture_path("ca.crt")),
    };
    let server_tls = startup::build_server_tls_config(Some(&tls_config))
        .expect("build mTLS config")
        .expect("must be Some");

    let server = start_server(Some(server_tls)).await;
    let ca_pem = read_fixture("ca.crt");
    let untrusted_cert = read_fixture("untrusted_client.crt");
    let untrusted_key = read_fixture("untrusted_client.key");

    // Client connects presenting client certificate signed by untrusted CA.
    let client_result =
        connect_tls(server.addr, ca_pem, Some((untrusted_cert, untrusted_key))).await;
    match client_result {
        Err(_) => {} // Connection handshake failed as expected.
        Ok(mut client) => {
            let rpc_result = client.commit(Request::new(pb::CommitRequest::default())).await;
            assert!(
                rpc_result.is_err(),
                "mTLS server must reject RPC with untrusted client certificate"
            );
        }
    }

    server.stop().await;
}

#[test]
fn test_missing_certificate_file_fails_with_clear_error() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("nonexistent.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: None,
    };
    let err = startup::build_server_tls_config(Some(&tls_config))
        .expect_err("nonexistent cert path must fail");

    match err {
        TlsConfigError::IoError { setting, path, .. } => {
            assert_eq!(setting, ENV_TLS_CERT_PATH);
            assert_eq!(path, fixture_path("nonexistent.crt"));
        }
        other => panic!("expected IoError, got: {other:?}"),
    }
}

#[test]
fn test_missing_key_file_fails_with_clear_error() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("server.crt"),
        key_path: fixture_path("nonexistent.key"),
        client_ca_path: None,
    };
    let err = startup::build_server_tls_config(Some(&tls_config))
        .expect_err("nonexistent key path must fail");

    match err {
        TlsConfigError::IoError { setting, path, .. } => {
            assert_eq!(setting, ENV_TLS_KEY_PATH);
            assert_eq!(path, fixture_path("nonexistent.key"));
        }
        other => panic!("expected IoError, got: {other:?}"),
    }
}

#[test]
fn test_invalid_pem_format_fails_with_clear_error() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("corrupt.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: None,
    };
    let err = startup::build_server_tls_config(Some(&tls_config))
        .expect_err("corrupt cert PEM must fail");

    match err {
        TlsConfigError::InvalidPem { setting, path, .. } => {
            assert_eq!(setting, ENV_TLS_CERT_PATH);
            assert_eq!(path, fixture_path("corrupt.crt"));
        }
        other => panic!("expected InvalidPem, got: {other:?}"),
    }
}

#[test]
fn test_empty_pem_file_fails_with_clear_error() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("empty.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: None,
    };
    let err =
        startup::build_server_tls_config(Some(&tls_config)).expect_err("empty cert PEM must fail");

    match err {
        TlsConfigError::InvalidPem { setting, path, reason } => {
            assert_eq!(setting, ENV_TLS_CERT_PATH);
            assert_eq!(path, fixture_path("empty.crt"));
            assert_eq!(reason, "file is empty");
        }
        other => panic!("expected InvalidPem, got: {other:?}"),
    }
}

#[test]
fn test_missing_client_ca_file_fails_with_clear_error() {
    let tls_config = TlsConfig {
        cert_path: fixture_path("server.crt"),
        key_path: fixture_path("server.key"),
        client_ca_path: Some(fixture_path("nonexistent_ca.crt")),
    };
    let err = startup::build_server_tls_config(Some(&tls_config))
        .expect_err("nonexistent CA path must fail");

    match err {
        TlsConfigError::IoError { setting, path, .. } => {
            assert_eq!(setting, ENV_TLS_CLIENT_CA_PATH);
            assert_eq!(path, fixture_path("nonexistent_ca.crt"));
        }
        other => panic!("expected IoError, got: {other:?}"),
    }
}
