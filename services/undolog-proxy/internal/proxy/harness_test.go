// Package proxy tests the HTTP ingress for the UndoLog MCP interceptor.
//
// The harness in this file serves a scripted engine over an in-memory
// listener and dials it with the production engine client, so the full
// proxy stack exercises the real gRPC codec without ports, Docker, or
// PostgreSQL.
package proxy

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"

	eng "undolog-proxy/internal/engine"
	"undolog-proxy/internal/engine/pb"
	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
)

// scriptedEngine implements the generated UndoLogEngine server with canned
// answers. Intercept, Commit, and Approve record the request they received
// and return the scripted response or error, so a test asserts exactly what
// crossed the wire. The remaining RPCs keep the generated Unimplemented
// answers, which surface as an explicit gRPC status if a flow calls one. An
// Intercept with no scripted response returns an empty response, which the
// proxy treats as an unknown outcome.
type scriptedEngine struct {
	pb.UnimplementedUndoLogEngineServer

	mu sync.Mutex

	interceptResp *pb.InterceptResponse
	interceptErr  error
	commitErr     error
	approveResp   *pb.ApproveResponse
	interceptReq  *pb.InterceptRequest
	commitReq     *pb.CommitRequest
	approveReq    *pb.ApproveRequest
}

// Intercept records the request and returns the scripted answer.
func (e *scriptedEngine) Intercept(_ context.Context, req *pb.InterceptRequest) (*pb.InterceptResponse, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.interceptReq = req
	if e.interceptErr != nil {
		return nil, e.interceptErr
	}
	if e.interceptResp != nil {
		return e.interceptResp, nil
	}
	return &pb.InterceptResponse{}, nil
}

// Commit records the request and returns the scripted error, if any.
func (e *scriptedEngine) Commit(_ context.Context, req *pb.CommitRequest) (*pb.CommitResponse, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.commitReq = req
	if e.commitErr != nil {
		return nil, e.commitErr
	}
	return &pb.CommitResponse{}, nil
}

// Approve records the request and returns the scripted execution data.
func (e *scriptedEngine) Approve(_ context.Context, req *pb.ApproveRequest) (*pb.ApproveResponse, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.approveReq = req
	if e.approveResp == nil {
		return &pb.ApproveResponse{}, nil
	}
	return e.approveResp, nil
}

// engineHarness runs a scripted engine over an in-memory bufconn listener
// and holds the production engine client dialed into it, so a test wires the
// full proxy stack against the real gRPC codec without opening a port.
type engineHarness struct {
	server   *grpc.Server
	listener *bufconn.Listener
	conn     *grpc.ClientConn
	client   *eng.Client
}

// newEngineHarness starts the scripted engine and dials it the way the
// production client dials the Rust engine, except the transport is
// in-memory. The client and server are torn down when the test ends.
func newEngineHarness(t *testing.T, script *scriptedEngine) *engineHarness {
	t.Helper()
	listener := bufconn.Listen(1 << 20)
	server := grpc.NewServer()
	pb.RegisterUndoLogEngineServer(server, script)
	// Serve returns nil once Stop closes the listener during cleanup; any
	// real failure surfaces on the first RPC instead, so the error is
	// dropped here.
	go func() { _ = server.Serve(listener) }()

	conn, err := grpc.NewClient(
		"passthrough:///undolog-test-engine",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return listener.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("dial in-process engine: %v", err)
	}
	h := &engineHarness{
		server:   server,
		listener: listener,
		conn:     conn,
		client:   eng.NewClientWithTransport("undolog-test-engine", eng.RetryConfig{MaxAttempts: 1}, eng.NewGRPCTransport(conn), nil),
	}
	t.Cleanup(h.stop)
	return h
}

// stop tears down the client, server, and listener. Close failures during
// teardown cannot change a test result, so they are dropped.
func (h *engineHarness) stop() {
	_ = h.client.Close()
	_ = h.conn.Close()
	h.server.Stop()
	_ = h.listener.Close()
}

// newProxyTestServer builds the proxy server stack around an injected engine
// client with the shared in-process test configuration: one trusted API key,
// no rate limit, and no plugins.
func newProxyTestServer(t *testing.T, engineClient protocol.EngineClient, executor ToolExecutor) *Server {
	t.Helper()
	cfg := Config{
		ListenAddr:            ":0",
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}
	server, err := NewServer(cfg, engineClient, executor, metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	return server
}
