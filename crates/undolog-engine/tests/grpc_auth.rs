//! Round-trip authentication tests for the engine's gRPC service.
//!
//! The token interceptor runs before the service, so a stub engine that
//! answers every RPC with a canned response keeps these tests free of a
//! database.

use std::net::{SocketAddr, TcpListener as StdTcpListener};
use std::time::Duration;

use tokio::sync::oneshot;
use tonic::transport::{Channel, Server};
use tonic::{Request, Response, Status};

use undolog_engine::grpc::auth;
use undolog_engine::grpc::pb;
use undolog_engine::grpc::pb::undo_log_engine_client::UndoLogEngineClient;
use undolog_engine::grpc::pb::undo_log_engine_server::UndoLogEngineServer;

/// Configured token for the enforcing test servers.
const CONFIGURED_TOKEN: &str = "test-secret-token";

// ── Stub engine ────────────────────────────────────────────────────────────

/// Engine stand-in: authentication is the unit under test, so every RPC
/// answers with a canned response.
struct StubEngine;

#[tonic::async_trait]
impl pb::undo_log_engine_server::UndoLogEngine for StubEngine {
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
}

// ── Server harness ─────────────────────────────────────────────────────────

/// Running stub server: shutdown is explicit, so a test never leaves the
/// task behind and a clean exit is asserted.
struct AuthServer {
    addr: SocketAddr,
    shutdown: oneshot::Sender<()>,
    task: tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
}

impl AuthServer {
    /// Signal shutdown and assert the server exits cleanly.
    async fn stop(self) {
        let _ = self.shutdown.send(());
        let result = self.task.await.expect("server task must not panic");
        assert!(result.is_ok(), "server must shut down cleanly: {result:?}");
    }
}

/// Start the auth-wrapped stub on an ephemeral loopback port.
async fn start_server(token: Option<String>) -> AuthServer {
    // The port is claimed only to learn a free address; tonic binds it
    // again a moment later. The connect retry below absorbs that gap.
    let probe = StdTcpListener::bind("127.0.0.1:0").expect("probe an ephemeral loopback port");
    let addr: SocketAddr = probe.local_addr().expect("read the ephemeral address");
    drop(probe);

    let (shutdown_tx, shutdown_rx) = oneshot::channel::<()>();
    let service = auth::authenticated(UndoLogEngineServer::new(StubEngine), token);
    let task = tokio::spawn(async move {
        Server::builder()
            .add_service(service)
            .serve_with_shutdown(addr, async move {
                let _ = shutdown_rx.await;
            })
            .await
    });
    AuthServer { addr, shutdown: shutdown_tx, task }
}

/// Connect to the stub, retrying while its listener comes up.
async fn connect(addr: SocketAddr) -> UndoLogEngineClient<Channel> {
    let url = format!("http://{addr}");
    for _ in 0..100 {
        if let Ok(client) = UndoLogEngineClient::connect(url.clone()).await {
            return client;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("stub server did not accept connections at {addr}");
}

/// Build an Intercept request carrying the bearer credential.
fn bearer_request(token: &str) -> Request<pb::InterceptRequest> {
    let mut request = Request::new(pb::InterceptRequest::default());
    let credential = format!("Bearer {token}");
    request
        .metadata_mut()
        .insert("authorization", credential.parse().expect("bearer value must be valid"));
    request
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[tokio::test]
async fn without_a_configured_token_every_call_succeeds() {
    let server = start_server(None).await;
    let mut client = connect(server.addr).await;

    assert!(client.intercept(Request::new(pb::InterceptRequest::default())).await.is_ok());
    assert!(client.commit(Request::new(pb::CommitRequest::default())).await.is_ok());
    assert!(client.fail(Request::new(pb::FailRequest::default())).await.is_ok());
    assert!(client.approve(Request::new(pb::ApproveRequest::default())).await.is_ok());
    assert!(client.reject(Request::new(pb::RejectRequest::default())).await.is_ok());
    assert!(client
        .list_pending_approvals(Request::new(pb::ListPendingApprovalsRequest::default()))
        .await
        .is_ok());

    server.stop().await;
}

#[tokio::test]
async fn with_a_configured_token_the_matching_bearer_passes() {
    let server = start_server(Some(CONFIGURED_TOKEN.to_string())).await;
    let mut client = connect(server.addr).await;

    let intercepted = client.intercept(bearer_request(CONFIGURED_TOKEN)).await;
    assert!(intercepted.is_ok(), "matching bearer must pass: {intercepted:?}");

    server.stop().await;
}

#[tokio::test]
async fn with_a_configured_token_a_missing_token_is_rejected() {
    let server = start_server(Some(CONFIGURED_TOKEN.to_string())).await;
    let mut client = connect(server.addr).await;

    let status = client
        .intercept(Request::new(pb::InterceptRequest::default()))
        .await
        .expect_err("call without the token must be rejected");
    assert_eq!(status.code(), tonic::Code::Unauthenticated);
    let message = status.message();
    assert!(message.contains("authorization"), "message must name the header: {message}");
    assert!(message.contains("Bearer"), "message must name the expected form: {message}");
    assert!(!message.contains(CONFIGURED_TOKEN), "message must not echo the configured token");

    server.stop().await;
}

#[tokio::test]
async fn with_a_configured_token_a_wrong_token_is_rejected_without_echo() {
    const WRONG_TOKEN: &str = "wrong-token-value";
    let server = start_server(Some(CONFIGURED_TOKEN.to_string())).await;
    let mut client = connect(server.addr).await;

    let status = client
        .intercept(bearer_request(WRONG_TOKEN))
        .await
        .expect_err("wrong token must be rejected");
    assert_eq!(status.code(), tonic::Code::Unauthenticated);
    let message = status.message();
    assert!(!message.contains(WRONG_TOKEN), "message must not echo the presented token");
    assert!(!message.contains(CONFIGURED_TOKEN), "message must not echo the configured token");

    server.stop().await;
}

#[tokio::test]
async fn with_a_configured_token_every_rpc_is_guarded() {
    let server = start_server(Some(CONFIGURED_TOKEN.to_string())).await;
    let mut client = connect(server.addr).await;

    let status = client
        .list_pending_approvals(Request::new(pb::ListPendingApprovalsRequest::default()))
        .await
        .expect_err("listing without the token must be rejected");
    assert_eq!(status.code(), tonic::Code::Unauthenticated);

    server.stop().await;
}
