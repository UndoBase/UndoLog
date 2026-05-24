// Package proxy tests the HTTP ingress for the UndoLog MCP interceptor.
//
// The tests cover routing, auth middleware, approval flows, and the canonical
// signature behavior used to deduplicate tool calls.
package proxy

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"undolog-proxy/internal/approval"
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
}

// Intercept returns the canned intercept response for the mock engine client.
func (m *mockEngineClient) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	m.interceptReq = req
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
func (m *mockEngineClient) Approve(ctx context.Context, req protocol.ApproveRequest) error {
	return nil
}

// Reject is a no-op stub used to satisfy the engine client interface.
func (m *mockEngineClient) Reject(ctx context.Context, req protocol.RejectRequest) error { return nil }

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
		h := NewHandler(engine, executor, store, b, time.Second, nil)

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
		}), approval.NewStore(), sse.NewBroadcaster(8), time.Second, nil)

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
		}), approval.NewStore(), sse.NewBroadcaster(8), time.Second, nil)

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
	}), nil)
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
	}), nil)
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
	eventReq := httptest.NewRequest(http.MethodGet, "/events?org_id=org-1", nil).WithContext(ctx)
	eventReq.Header.Set("X-Api-Key", "key-1")
	eventRec := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		server.httpSrv.Handler.ServeHTTP(eventRec, eventReq)
		close(done)
	}()
	time.Sleep(30 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("events handler did not stop")
	}
	if eventRec.Code != http.StatusOK {
		t.Fatalf("unexpected events status: %d", eventRec.Code)
	}
}
