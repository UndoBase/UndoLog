// Package proxy tests the HTTP ingress for the UndoLog MCP interceptor.
//
// The tests cover routing, auth middleware, approval flows, and the canonical
// signature behavior used to deduplicate tool calls.
package proxy

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"undolog-proxy/internal/approval"
	eng "undolog-proxy/internal/engine"
	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

type mockEngineClient struct {
	interceptResp protocol.InterceptResponse
	interceptErr  error
	commitErr     error
	failErr       error
	interceptReq  protocol.InterceptRequest
	commitReq     protocol.CommitRequest
	failReq       protocol.FailRequest
	lastCtx       context.Context
	pending       []protocol.ApprovalRecord
}

// Intercept returns the canned intercept response for the mock engine client.
func (m *mockEngineClient) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	m.interceptReq = req
	m.lastCtx = ctx
	return m.interceptResp, m.interceptErr
}

// Commit records the commit request and returns the configured error.
func (m *mockEngineClient) Commit(ctx context.Context, req protocol.CommitRequest) error {
	m.commitReq = req
	return m.commitErr
}

// Fail records the fail request and returns the configured error.
func (m *mockEngineClient) Fail(ctx context.Context, req protocol.FailRequest) error {
	m.failReq = req
	return m.failErr
}

// Approve is a no-op stub used to satisfy the engine client interface.
func (m *mockEngineClient) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	return protocol.ApproveResponse{}, nil
}

// Reject is a no-op stub used to satisfy the engine client interface.
func (m *mockEngineClient) Reject(ctx context.Context, req protocol.RejectRequest) error { return nil }

// ListPendingApprovals returns the configured pending records.
func (m *mockEngineClient) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	return protocol.ListPendingApprovalsResponse{Records: m.pending}, nil
}

// Close is a no-op stub used to satisfy the engine client interface.
func (m *mockEngineClient) Close() error { return nil }

// TestCanonicalSignatureIsStable verifies argument ordering does not affect signatures.
func TestCanonicalSignatureIsStable(t *testing.T) {
	a := `{"b":2,"a":{"z":1,"y":true}}`
	b := `{"a":{"y":true,"z":1},"b":2}`

	sigA, err := CanonicalSignature("search", json.RawMessage(a))
	if err != nil {
		t.Fatalf("canonical signature a: %v", err)
	}
	sigB, err := CanonicalSignature("search", json.RawMessage(b))
	if err != nil {
		t.Fatalf("canonical signature b: %v", err)
	}
	if sigA != sigB {
		t.Fatalf("expected stable canonical signature, got %q and %q", sigA, sigB)
	}
}

// TestLoadConfigDefaultsAndAPIKeys verifies env parsing and default configuration.
func TestLoadConfigDefaultsAndAPIKeys(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_LISTEN_ADDR", ":9090")
	t.Setenv("UNDOLOG_PROXY_ENGINE_GRPC_ADDR", "engine:50051")
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1,k2=o2")
	t.Setenv("UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS", "9")
	t.Setenv("UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS", "5")
	t.Setenv("UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS", "250")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ListenAddr != ":9090" || cfg.EngineGRPCAddr != "engine:50051" {
		t.Fatalf("unexpected config: %+v", cfg)
	}
	if got := cfg.TrustedAPIKeys["k2"]; got != "o2" {
		t.Fatalf("unexpected api key mapping: %v", cfg.TrustedAPIKeys)
	}
	if cfg.RequestTimeout != 9*time.Second {
		t.Fatalf("unexpected request timeout: %s", cfg.RequestTimeout)
	}
	if cfg.EngineRetryMaxAttempts != 5 {
		t.Fatalf("unexpected engine retry attempts: %d", cfg.EngineRetryMaxAttempts)
	}
	if cfg.EngineRetryBackoff != 250*time.Millisecond {
		t.Fatalf("unexpected engine retry backoff: %s", cfg.EngineRetryBackoff)
	}
	if cfg.MaxBodyBytes != 1<<20 {
		t.Fatalf("unexpected max body bytes: %d", cfg.MaxBodyBytes)
	}
	if cfg.ReadHeaderTimeout != 15*time.Second || cfg.IdleTimeout != 60*time.Second || cfg.MaxHeaderBytes != 1<<20 {
		t.Fatalf("unexpected server hardening defaults: %+v", cfg)
	}
}

// TestLoadConfigRejectsEmptyAPIKeys verifies LoadConfig fails fast when no API
// keys are configured, so a deployment cannot start in a state where every
// request would be rejected.
func TestLoadConfigRejectsEmptyAPIKeys(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "")
	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "http://upstream:9091")
	_, err := LoadConfig()
	if err == nil || !strings.Contains(err.Error(), "UNDOLOG_PROXY_API_KEYS") {
		t.Fatalf("expected a missing API keys error, got %v", err)
	}
}

// TestLoadConfigValidatesUpstreamURL verifies the configured upstream endpoint
// must be an absolute http(s) URL, while an unset one is still accepted.
func TestLoadConfigValidatesUpstreamURL(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1")
	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "not a url")
	if _, err := LoadConfig(); err == nil || !strings.Contains(err.Error(), "UNDOLOG_PROXY_UPSTREAM_TOOL_URL") {
		t.Fatalf("expected an upstream URL error, got %v", err)
	}

	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "ftp://tool-server:9091")
	if _, err := LoadConfig(); err == nil || !strings.Contains(err.Error(), "http(s)") {
		t.Fatalf("expected a scheme error, got %v", err)
	}

	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "http://tool-server:9091")
	if _, err := LoadConfig(); err != nil {
		t.Fatalf("valid upstream URL rejected: %v", err)
	}

	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "")
	if _, err := LoadConfig(); err != nil {
		t.Fatalf("empty upstream URL should still load, got %v", err)
	}
}

// TestMaxBodyBytesEnvParses verifies the body cap env var overrides the default.
func TestMaxBodyBytesEnvParses(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1")
	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "")
	t.Setenv("UNDOLOG_PROXY_MAX_BODY_BYTES", "4096")
	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.MaxBodyBytes != 4096 {
		t.Fatalf("expected max body bytes 4096, got %d", cfg.MaxBodyBytes)
	}
}

// TestServerHardeningValues verifies the HTTP server picks up the read-header,
// idle, and header-size limits from the config.
func TestServerHardeningValues(t *testing.T) {
	cfg := Config{
		ListenAddr:        ":0",
		ReadTimeout:       5 * time.Second,
		ReadHeaderTimeout: 3 * time.Second,
		IdleTimeout:       45 * time.Second,
		MaxHeaderBytes:    8192,
		EngineGRPCAddr:    "engine:50051",
		UpstreamToolURL:   "http://upstream",
		TrustedAPIKeys:    map[string]string{"key-1": "org-1"},
	}
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	if server.httpSrv.ReadHeaderTimeout != 3*time.Second {
		t.Fatalf("read header timeout: %s", server.httpSrv.ReadHeaderTimeout)
	}
	if server.httpSrv.IdleTimeout != 45*time.Second {
		t.Fatalf("idle timeout: %s", server.httpSrv.IdleTimeout)
	}
	if server.httpSrv.MaxHeaderBytes != 8192 {
		t.Fatalf("max header bytes: %d", server.httpSrv.MaxHeaderBytes)
	}
}

// TestToolCallBodySizeLimit verifies an oversized /mcp/tool_call body returns
// 413 instead of being decoded, and a body within the limit is accepted.
func TestToolCallBodySizeLimit(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		MaxBodyBytes:          256,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}
	server, err := NewServer(cfg, &mockEngineClient{
		interceptResp: protocol.InterceptResponse{Outcome: protocol.InterceptExecute, EffectID: "eff-1"},
	}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{Success: true}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	big := strings.Repeat("x", 4096)
	body := `{"session_id":"sess-1","tool_name":"search","args":{"payload":"` + big + `"}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	req.Header.Set("X-Api-Key", "key-1")
	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected 413 for oversized body, got %d (%s)", rec.Code, rec.Body.String())
	}

	okReq := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(`{"session_id":"sess-1","tool_name":"search","args":{}}`))
	okReq.Header.Set("X-Api-Key", "key-1")
	okRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(okRec, okReq)
	if okRec.Code != http.StatusOK {
		t.Fatalf("expected 200 for in-limit body, got %d (%s)", okRec.Code, okRec.Body.String())
	}
}

// TestMiddlewareAuthAndRequestID verifies auth, request IDs, and logging middleware.
func TestMiddlewareAuthAndRequestID(t *testing.T) {
	stack := NewMiddlewareStack(nil, map[string]string{"key-1": "org-1"})

	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := orgIDFrom(r.Context()); got != "org-1" {
			t.Fatalf("org id missing, got %q", got)
		}
		if got := requestIDFrom(r.Context()); got == "" {
			t.Fatal("request id missing")
		}
		w.WriteHeader(http.StatusOK)
	})

	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", nil)
	req.Header.Set("X-Api-Key", "key-1")
	rec := httptest.NewRecorder()

	handler := stack.PanicRecovery(stack.RequestID(stack.StructuredLogging(stack.Auth(next))))
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", rec.Code)
	}
	if rec.Header().Get("X-Request-Id") == "" {
		t.Fatal("expected X-Request-Id header")
	}
}

// TestMiddlewareAuthRejectsUnknownAndMissingKeys verifies the digest-based auth
// middleware still enforces the 401/403 contract for missing and unknown keys.
func TestMiddlewareAuthRejectsUnknownAndMissingKeys(t *testing.T) {
	stack := NewMiddlewareStack(nil, map[string]string{"key-1": "org-1"})
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	unknown := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	unknown.Header.Set("X-Api-Key", "not-a-real-key")
	uRec := httptest.NewRecorder()
	stack.Auth(next).ServeHTTP(uRec, unknown)
	if uRec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for unknown key, got %d", uRec.Code)
	}

	missing := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	mRec := httptest.NewRecorder()
	stack.Auth(next).ServeHTTP(mRec, missing)
	if mRec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for missing key, got %d", mRec.Code)
	}
}

// TestHandlerRoutesExecuteReplayAndApproval covers the handler's three outcome paths.
func TestHandlerRoutesExecuteReplayAndApproval(t *testing.T) {
	t.Run("execute", func(t *testing.T) {
		engine := &mockEngineClient{
			interceptResp: protocol.InterceptResponse{
				Outcome:  protocol.InterceptExecute,
				EffectID: "eff-1",
			},
		}
		b := sse.NewBroadcaster(8)
		store := approval.NewStore()
		executor := ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
			return protocol.ToolResult{Success: true, Output: json.RawMessage(`{"ok":true}`)}, nil
		})
		h := NewHandler(engine, executor, store, b, time.Second, 1<<20, nil)

		body := `{"session_id":"sess-1","tool_name":"search","args":{"q":"undo"}}`
		req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
		req = req.WithContext(context.WithValue(req.Context(), ctxKeyOrgID, "org-1"))
		rec := httptest.NewRecorder()

		h.ServeHTTP(rec, req)

		if rec.Code != http.StatusOK {
			t.Fatalf("unexpected status: %d", rec.Code)
		}
		if engine.commitReq.EffectID != "eff-1" {
			t.Fatalf("commit not called with effect id: %+v", engine.commitReq)
		}
	})

	t.Run("replay", func(t *testing.T) {
		engine := &mockEngineClient{
			interceptResp: protocol.InterceptResponse{
				Outcome:      protocol.InterceptReplay,
				EffectID:     "eff-2",
				CachedResult: &protocol.ToolResult{Success: true, Output: json.RawMessage(`{"cached":true}`)},
			},
		}
		h := NewHandler(engine, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
			t.Fatal("executor should not be called")
			return protocol.ToolResult{}, nil
		}), approval.NewStore(), sse.NewBroadcaster(8), time.Second, 1<<20, nil)

		req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(`{"session_id":"sess-1","tool_name":"search","args":{}}`))
		req = req.WithContext(context.WithValue(req.Context(), ctxKeyOrgID, "org-1"))
		rec := httptest.NewRecorder()

		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("unexpected status: %d", rec.Code)
		}
	})

	t.Run("approval", func(t *testing.T) {
		engine := &mockEngineClient{
			interceptResp: protocol.InterceptResponse{
				Outcome:    protocol.InterceptAwaitingApproval,
				ApprovalID: "app-1",
			},
		}
		h := NewHandler(engine, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
			t.Fatal("executor should not be called")
			return protocol.ToolResult{}, nil
		}), approval.NewStore(), sse.NewBroadcaster(8), time.Second, 1<<20, nil)

		req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(`{"session_id":"sess-1","tool_name":"delete_user","args":{}}`))
		req = req.WithContext(context.WithValue(req.Context(), ctxKeyOrgID, "org-1"))
		rec := httptest.NewRecorder()

		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusAccepted {
			t.Fatalf("unexpected status: %d", rec.Code)
		}
	})
}

// TestHealthEndpointBypassesAuth verifies the health endpoint remains public.
func TestHealthEndpointBypassesAuth(t *testing.T) {
	cfg := Config{
		ListenAddr:      ":0",
		ReadTimeout:     time.Second,
		WriteTimeout:    time.Second,
		ShutdownTimeout: time.Second,
		RequestTimeout:  time.Second,
		EngineGRPCAddr:  "engine:50051",
		UpstreamToolURL: "http://upstream",
		TrustedAPIKeys:  map[string]string{},
	}

	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", rec.Code)
	}
	if !bytes.Contains(rec.Body.Bytes(), []byte(`"status":"ok"`)) {
		t.Fatalf("unexpected body: %s", rec.Body.String())
	}
}

// signalRecorder records the response while closing a channel on the first
// payload write, so tests can wait for an SSE frame without racing the handler.
type signalRecorder struct {
	*httptest.ResponseRecorder
	sig  chan struct{}
	once sync.Once
}

func (r *signalRecorder) Write(b []byte) (int, error) {
	n, err := r.ResponseRecorder.Write(b)
	r.once.Do(func() { close(r.sig) })
	return n, err
}

// TestServerRoutesApprovalsAndEvents verifies approval and SSE routes are wired.
func TestServerRoutesApprovalsAndEvents(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
		ReadTimeout:           time.Second,
		WriteTimeout:          time.Second,
		ShutdownTimeout:       time.Second,
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}

	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	approvalReq := httptest.NewRequest(http.MethodGet, "/approvals?state=pending", nil)
	approvalReq.Header.Set("X-Api-Key", "key-1")
	approvalRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(approvalRec, approvalReq)
	if approvalRec.Code != http.StatusOK {
		t.Fatalf("unexpected approvals status: %d", approvalRec.Code)
	}

	ctx, cancel := context.WithCancel(context.Background())
	eventReq := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(ctx)
	eventReq.Header.Set("X-Api-Key", "key-1")
	eventRec := &signalRecorder{ResponseRecorder: httptest.NewRecorder(), sig: make(chan struct{})}
	done := make(chan struct{})
	go func() {
		server.httpSrv.Handler.ServeHTTP(eventRec, eventReq)
		close(done)
	}()
	// Emit until a frame is written; the first emit landing after the handler
	// subscribes is enough, since the handler stays subscribed until canceled.
	emitted := false
	for i := 0; i < 10 && !emitted; i++ {
		server.broadcaster.Emit(sse.Event{Type: sse.EventEffectCommitted, OrgID: "org-1", SessionID: "sess-1", EffectID: "eff-1"})
		select {
		case <-eventRec.sig:
			emitted = true
		case <-time.After(20 * time.Millisecond):
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("events handler did not stop")
	}
	body := eventRec.Body.String()
	if eventRec.Code != http.StatusOK {
		t.Fatalf("unexpected events status: %d", eventRec.Code)
	}
	if strings.Contains(body, "streaming unsupported") {
		t.Fatalf("SSE streaming must be supported, got %q", body)
	}
	if !strings.Contains(body, "event: effect_committed") {
		t.Fatalf("expected an SSE event frame in body, got %q", body)
	}
	if !strings.Contains(body, `"effect_id":"eff-1"`) {
		t.Fatalf("expected effect id in SSE data, got %q", body)
	}
}

// TestServerSSEStreamsPastWriteTimeout verifies /events survives the server
// WriteTimeout by clearing the stream write deadline.
func TestServerSSEStreamsPastWriteTimeout(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
		ReadTimeout:           time.Second,
		WriteTimeout:          300 * time.Millisecond,
		ShutdownTimeout:       time.Second,
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}

	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go func() { _ = server.httpSrv.Serve(listener) }()
	defer func() { _ = server.httpSrv.Close() }()

	conn, err := net.Dial("tcp", listener.Addr().String())
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()

	req := httptest.NewRequest(http.MethodGet, "/events", nil)
	req.Header.Set("X-Api-Key", "key-1")
	if err := req.Write(conn); err != nil {
		t.Fatalf("write request: %v", err)
	}
	resp, err := http.ReadResponse(bufio.NewReader(conn), req)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected 200, got %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("unexpected content type: %q", ct)
	}

	// Emit well past the WriteTimeout from a side goroutine while the main
	// goroutine reads the stream; the frame must still be delivered.
	go func() {
		time.Sleep(450 * time.Millisecond)
		server.broadcaster.Emit(sse.Event{Type: sse.EventEffectCommitted, OrgID: "org-1", SessionID: "sess-1", EffectID: "eff-1"})
	}()

	// Bound the reads so a broken stream fails fast instead of hanging.
	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	defer func() { _ = conn.SetReadDeadline(time.Time{}) }()

	br := bufio.NewReader(resp.Body)
	var sawEvent, sawData bool
	for !sawEvent || !sawData {
		line, err := br.ReadString('\n')
		if err != nil {
			t.Fatalf("stream ended before SSE frame arrived: %v", err)
		}
		if strings.Contains(line, "event: effect_committed") {
			sawEvent = true
		}
		if strings.Contains(line, `"effect_id":"eff-1"`) {
			sawData = true
		}
	}

	// Stop reading the live stream. Close the connection first so the deferred
	// body close does not block draining the endless chunked response.
	conn.Close()
}

// TestServerReconcilePopulatesApprovals verifies the startup reconciliation
// restores pending approvals from the engine so a proxy restart does not orphan
// them.
func TestServerReconcilePopulatesApprovals(t *testing.T) {
	engine := &mockEngineClient{
		pending: []protocol.ApprovalRecord{
			{ApprovalID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{"id":"u1"}`), CreatedAtUnix: 1700000000000},
		},
	}
	cfg := Config{
		ListenAddr:            ":0",
		RequestTimeout:        time.Second,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		DashboardEventBufSize: 8,
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}
	server, err := NewServer(cfg, engine, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	server.reconcileOnce(context.Background())

	req := httptest.NewRequest(http.MethodGet, "/approvals?state=pending", nil)
	req.Header.Set("X-Api-Key", "key-1")
	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", rec.Code)
	}
	var listed []approval.Record
	if err := json.Unmarshal(rec.Body.Bytes(), &listed); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	if len(listed) != 1 || listed[0].ID != "ap-1" {
		t.Fatalf("expected reconciled approval ap-1, got %+v", listed)
	}
}

// TestHTTPToolExecutorUsesRequestTimeout verifies the executor client honors
// the configured timeout instead of the hardcoded 30 seconds, so a lower
// RequestTimeout setting actually shortens upstream waits.
func TestHTTPToolExecutorUsesRequestTimeout(t *testing.T) {
	release := make(chan struct{})
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		<-release
	}))
	defer func() {
		close(release)
		slow.Close()
	}()

	executor, err := NewHTTPToolExecutor(slow.URL, 50*time.Millisecond)
	if err != nil {
		t.Fatalf("new executor: %v", err)
	}

	start := time.Now()
	_, err = executor.Execute(context.Background(), protocol.ToolCall{ToolName: "search", Args: json.RawMessage(`{}`)})
	elapsed := time.Since(start)
	if err == nil {
		t.Fatal("expected the upstream timeout to surface as an error")
	}
	if elapsed > time.Second {
		t.Fatalf("executor ignored the 50ms timeout, took %s", elapsed)
	}
}

// TestHTTPToolExecutorDecodesStructuredToolResult verifies a non-2xx upstream
// response carrying a ToolResult body is delivered as a result instead of a
// transport error, so a logical tool failure does not surface as a 502.
func TestHTTPToolExecutorDecodesStructuredToolResult(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"success":false,"error":"customer not found"}`))
	}))
	defer upstream.Close()

	executor, err := NewHTTPToolExecutor(upstream.URL, time.Second)
	if err != nil {
		t.Fatalf("new executor: %v", err)
	}

	result, err := executor.Execute(context.Background(), protocol.ToolCall{ToolName: "get_customer", Args: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatalf("structured tool failure should not be a transport error: %v", err)
	}
	if result.Success {
		t.Fatal("expected success:false from the 404 ToolResult body")
	}
	if result.Error != "customer not found" {
		t.Fatalf("expected the upstream error text, got %q", result.Error)
	}
}

// TestHTTPToolExecutorKeepsTransportError verifies a non-2xx upstream response
// without a ToolResult body still surfaces as a transport error.
func TestHTTPToolExecutorKeepsTransportError(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("upstream exploded"))
	}))
	defer upstream.Close()

	executor, err := NewHTTPToolExecutor(upstream.URL, time.Second)
	if err != nil {
		t.Fatalf("new executor: %v", err)
	}

	_, err = executor.Execute(context.Background(), protocol.ToolCall{ToolName: "search", Args: json.RawMessage(`{}`)})
	if err == nil {
		t.Fatal("expected a transport error for a non-ToolResult 500 body")
	}
}

// TestHandlerCommitsLogicalToolFailure verifies a structured success:false
// result from the upstream is committed to the engine and returned to the
// caller instead of producing a 502 tool_error.
func TestHandlerCommitsLogicalToolFailure(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"success":false,"error":"customer not found"}`))
	}))
	defer upstream.Close()

	executor, err := NewHTTPToolExecutor(upstream.URL, time.Second)
	if err != nil {
		t.Fatalf("new executor: %v", err)
	}
	engine := &mockEngineClient{
		interceptResp: protocol.InterceptResponse{
			Outcome:  protocol.InterceptExecute,
			EffectID: "eff-1",
		},
	}
	h := NewHandler(engine, executor, approval.NewStore(), sse.NewBroadcaster(8), time.Second, 1<<20, nil)

	body := `{"session_id":"sess-1","tool_name":"get_customer","args":{"id":"u1"}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	req = req.WithContext(context.WithValue(req.Context(), ctxKeyOrgID, "org-1"))
	rec := httptest.NewRecorder()

	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("structured failure should return 200, got %d (%s)", rec.Code, rec.Body.String())
	}
	if engine.failReq.EffectID != "" {
		t.Fatalf("logical failure must not call Fail, got %+v", engine.failReq)
	}
	if engine.commitReq.EffectID != "eff-1" {
		t.Fatalf("expected commit with effect eff-1, got %+v", engine.commitReq)
	}
	if engine.commitReq.Result.Success {
		t.Fatalf("commit must carry success:false, got %+v", engine.commitReq.Result)
	}
	var resp struct {
		Status string              `json:"status"`
		Result protocol.ToolResult `json:"result"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Status != "executed" || resp.Result.Success {
		t.Fatalf("unexpected response: status=%q result=%+v", resp.Status, resp.Result)
	}
}

// TestHandlerExecuteFailureCallsFailAndReports502 verifies a genuine transport
// error from the upstream still calls Fail and returns a 502 tool_error.
func TestHandlerExecuteFailureCallsFailAndReports502(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("upstream exploded"))
	}))
	defer upstream.Close()

	executor, err := NewHTTPToolExecutor(upstream.URL, time.Second)
	if err != nil {
		t.Fatalf("new executor: %v", err)
	}
	engine := &mockEngineClient{
		interceptResp: protocol.InterceptResponse{
			Outcome:  protocol.InterceptExecute,
			EffectID: "eff-1",
		},
	}
	h := NewHandler(engine, executor, approval.NewStore(), sse.NewBroadcaster(8), time.Second, 1<<20, nil)

	body := `{"session_id":"sess-1","tool_name":"search","args":{}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	req = req.WithContext(context.WithValue(req.Context(), ctxKeyOrgID, "org-1"))
	rec := httptest.NewRecorder()

	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("transport failure must return 502, got %d", rec.Code)
	}
	if engine.failReq.EffectID != "eff-1" {
		t.Fatalf("expected Fail with effect eff-1, got %+v", engine.failReq)
	}
}

// TestMetricRouteCapsApprovalCardinality verifies variable approval paths
// collapse to a normalized label per action instead of one series per id.
func TestMetricRouteCapsApprovalCardinality(t *testing.T) {
	for path, want := range map[string]string{
		"/mcp/tool_call":                     "/mcp/tool_call",
		"/events":                            "/events",
		"/approvals":                         "/approvals",
		"/approvals/111-222-333/approve":     "/approvals/{id}/approve",
		"/approvals/111-222-333/reject":      "/approvals/{id}/reject",
		"/approvals/111-222-333/approve/":    "/approvals/{id}/approve",
		"/approvals/not-a-decision/whatever": "/approvals/not-a-decision/whatever",
	} {
		if got := metricRoute(path); got != want {
			t.Errorf("metricRoute(%q) = %q, want %q", path, got, want)
		}
	}
}

// TestMetricsEndpointPublishesPrometheus verifies /metrics renders scrapable
// output reflecting observed requests without leaking any configuration.
func TestMetricsEndpointPublishesPrometheus(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
		ReadTimeout:           time.Second,
		WriteTimeout:          time.Second,
		ShutdownTimeout:       time.Second,
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}
	registry := metrics.NewRegistry()
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), registry, nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/approvals?state=pending", nil)
	req.Header.Set("X-Api-Key", "key-1")
	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("approvals status: %d", rec.Code)
	}

	metricsReq := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	metricsRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(metricsRec, metricsReq)

	if ct := metricsRec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/plain") {
		t.Fatalf("unexpected content type: %q", ct)
	}
	body := metricsRec.Body.String()
	for _, want := range []string{
		"# HELP undolog_proxy_http_requests_total",
		"# TYPE undolog_proxy_http_requests_total counter",
		`undolog_proxy_http_requests_total{route="/approvals",status="200"} 1`,
		"# TYPE undolog_proxy_http_request_duration_seconds histogram",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("expected metrics body to contain %q, got:\n%s", want, body)
		}
	}
	if strings.Contains(body, "engine_addr") || strings.Contains(body, "upstream_url") {
		t.Errorf("metrics must not leak configuration, got:\n%s", body)
	}
}

// TestHealthDoesNotLeakConfig verifies the liveness endpoint no longer echoes
// the engine address or upstream URL to unauthenticated callers.
func TestHealthDoesNotLeakConfig(t *testing.T) {
	cfg := Config{
		ListenAddr:      ":0",
		ReadTimeout:     time.Second,
		WriteTimeout:    time.Second,
		ShutdownTimeout: time.Second,
		RequestTimeout:  time.Second,
		EngineGRPCAddr:  "engine:50051",
		UpstreamToolURL: "http://upstream",
		TrustedAPIKeys:  map[string]string{},
	}
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", rec.Code)
	}
	body := rec.Body.String()
	if strings.Contains(body, "engine_addr") || strings.Contains(body, "upstream_url") || strings.Contains(body, "engine:50051") || strings.Contains(body, "http://upstream") {
		t.Fatalf("health leaked configuration, body: %s", body)
	}
}

// TestRequestIDPropagatedToEngine verifies the proxy request ID flows from the
// middleware into the engine call context so engine logs share the ID.
func TestRequestIDPropagatedToEngine(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
		ReadTimeout:           time.Second,
		WriteTimeout:          time.Second,
		ShutdownTimeout:       time.Second,
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}
	engine := &mockEngineClient{
		interceptResp: protocol.InterceptResponse{
			Outcome:  protocol.InterceptExecute,
			EffectID: "eff-1",
		},
	}
	server, err := NewServer(cfg, engine, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{Success: true}, nil
	}), metrics.NewRegistry(), nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	body := `{"session_id":"sess-1","tool_name":"search","args":{}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	req.Header.Set("X-Api-Key", "key-1")
	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("tool_call status: %d (%s)", rec.Code, rec.Body.String())
	}
	requestID := rec.Header().Get("X-Request-Id")
	if requestID == "" {
		t.Fatal("expected an X-Request-Id response header")
	}
	if got := eng.RequestIDFrom(engine.lastCtx); got != requestID {
		t.Fatalf("expected engine context to carry request id %q, got %q", requestID, got)
	}
}

// TestHTTPToolExecutorPublishesMetrics verifies upstream execution adds a
// duration histogram split by outcome to the shared registry.
func TestHTTPToolExecutorPublishesMetrics(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"success":true}`))
	}))
	defer upstream.Close()

	registry := metrics.NewRegistry()
	executor, err := NewHTTPToolExecutor(upstream.URL, time.Second)
	if err != nil {
		t.Fatalf("new executor: %v", err)
	}
	executor.SetMetrics(registry)

	if _, err := executor.Execute(context.Background(), protocol.ToolCall{ToolName: "search", Args: json.RawMessage(`{}`)}); err != nil {
		t.Fatalf("execute: %v", err)
	}

	rendered := registry.Render()
	if want := `undolog_proxy_executor_duration_seconds_bucket{result="success",le="+Inf"} 1`; !strings.Contains(rendered, want) {
		t.Errorf("expected %q, got:\n%s", want, rendered)
	}
}
