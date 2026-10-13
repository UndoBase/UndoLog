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
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"undolog-proxy/internal/approval"
	eng "undolog-proxy/internal/engine"
	"undolog-proxy/internal/engine/pb"
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

// TestLoadConfigEngineAuthToken verifies the engine token is read trimmed, the
// same way the engine normalizes its own copy, and that unset or blank values
// leave the credential disabled.
func TestLoadConfigEngineAuthToken(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want string
	}{
		{name: "unset", raw: "", want: ""},
		{name: "blank", raw: "   ", want: ""},
		{name: "plain", raw: "s3cret", want: "s3cret"},
		{name: "padded", raw: "  s3cret\n", want: "s3cret"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1")
			t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "")
			t.Setenv("UNDOLOG_PROXY_ENGINE_AUTH_TOKEN", tc.raw)
			cfg, err := LoadConfig()
			if err != nil {
				t.Fatalf("load config: %v", err)
			}
			if cfg.EngineAuthToken != tc.want {
				t.Fatalf("expected engine auth token %q, got %q", tc.want, cfg.EngineAuthToken)
			}
		})
	}
}

// TestLoadConfigParsesPluginList verifies UNDOLOG_PROXY_PLUGINS arrives as
// an ordered list with surrounding space trimmed and blank entries dropped,
// and that an unset variable runs no plugins.
func TestLoadConfigParsesPluginList(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want []string
	}{
		{name: "unset", raw: "", want: nil},
		{name: "blank", raw: "   ", want: nil},
		{name: "single", raw: "header", want: []string{"header"}},
		{name: "order and padding", raw: " second , first ", want: []string{"second", "first"}},
		{name: "empty entries dropped", raw: "header,,", want: []string{"header"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1")
			t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "")
			t.Setenv("UNDOLOG_PROXY_PLUGINS", tc.raw)
			cfg, err := LoadConfig()
			if err != nil {
				t.Fatalf("load config: %v", err)
			}
			if !slices.Equal(cfg.Plugins, tc.want) {
				t.Fatalf("plugins = %v, want %v", cfg.Plugins, tc.want)
			}
		})
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

// TestLoadConfigRateLimitPair verifies the rate limit settings default to
// disabled, parse a working pair together, and fail startup on a malformed
// or half-configured pair instead of silently dropping a configured limit.
func TestLoadConfigRateLimitPair(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1")
	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "")
	t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_RPS", "")
	t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_BURST", "")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.RateLimitRPS != 0 || cfg.RateLimitBurst != 0 {
		t.Fatalf("rate limit must be disabled by default, got rps=%v burst=%d", cfg.RateLimitRPS, cfg.RateLimitBurst)
	}

	t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_RPS", "12.5")
	t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_BURST", "20")
	cfg, err = LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.RateLimitRPS != 12.5 || cfg.RateLimitBurst != 20 {
		t.Fatalf("rate limit = (%v, %d), want (12.5, 20)", cfg.RateLimitRPS, cfg.RateLimitBurst)
	}

	cases := []struct {
		name    string
		rps     string
		burst   string
		wantVar string
	}{
		{name: "rate without burst", rps: "10", burst: "", wantVar: "UNDOLOG_PROXY_RATE_LIMIT_RPS"},
		{name: "burst without rate", rps: "", burst: "5", wantVar: "UNDOLOG_PROXY_RATE_LIMIT_RPS"},
		{name: "malformed rate", rps: "fast", burst: "5", wantVar: "UNDOLOG_PROXY_RATE_LIMIT_RPS"},
		{name: "negative rate", rps: "-1", burst: "5", wantVar: "UNDOLOG_PROXY_RATE_LIMIT_RPS"},
		{name: "malformed burst", rps: "1", burst: "lots", wantVar: "UNDOLOG_PROXY_RATE_LIMIT_BURST"},
		{name: "negative burst", rps: "1", burst: "-5", wantVar: "UNDOLOG_PROXY_RATE_LIMIT_BURST"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_RPS", tc.rps)
			t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_BURST", tc.burst)

			_, err := LoadConfig()
			if err == nil {
				t.Fatal("expected a startup error")
			}
			if !strings.Contains(err.Error(), tc.wantVar) {
				t.Fatalf("error %v must name %s", err, tc.wantVar)
			}
		})
	}
}

// TestLoadConfigEngineBackpressure verifies the engine backpressure settings
// default to disabled, parse a complete configuration, accept a lone gate,
// and fail startup on malformed, negative, or half-configured mechanisms
// instead of silently dropping a configured limit.
func TestLoadConfigEngineBackpressure(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "k1=o1")
	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "")
	for _, key := range backpressureKeys {
		t.Setenv(key, "")
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.MaxEngineRPCs != 0 || cfg.EngineQueueMax != 0 || cfg.EngineQueueWait != 0 ||
		cfg.EngineBreakerThreshold != 0 || cfg.EngineBreakerReset != 0 {
		t.Fatalf("backpressure must be disabled by default, got %+v", cfg)
	}

	t.Setenv("UNDOLOG_PROXY_MAX_ENGINE_RPCS", "8")
	t.Setenv("UNDOLOG_PROXY_ENGINE_QUEUE_MAX", "16")
	t.Setenv("UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS", "250")
	t.Setenv("UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD", "5")
	t.Setenv("UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS", "30")
	cfg, err = LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.MaxEngineRPCs != 8 || cfg.EngineQueueMax != 16 || cfg.EngineQueueWait != 250*time.Millisecond ||
		cfg.EngineBreakerThreshold != 5 || cfg.EngineBreakerReset != 30*time.Second {
		t.Fatalf("backpressure settings = %+v, want the configured values", cfg)
	}

	// A gate without a queue or breaker is a valid standalone mechanism.
	t.Setenv("UNDOLOG_PROXY_ENGINE_QUEUE_MAX", "")
	t.Setenv("UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS", "")
	t.Setenv("UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD", "")
	t.Setenv("UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS", "")
	if _, err := LoadConfig(); err != nil {
		t.Fatalf("a lone gate must load: %v", err)
	}

	cases := []struct {
		name    string
		entries map[string]string
		wantVar string
	}{
		{
			name:    "queue depth without wait budget",
			entries: map[string]string{"UNDOLOG_PROXY_ENGINE_QUEUE_MAX": "4"},
			wantVar: "UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS",
		},
		{
			name:    "wait budget without queue depth",
			entries: map[string]string{"UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS": "100"},
			wantVar: "UNDOLOG_PROXY_ENGINE_QUEUE_MAX",
		},
		{
			name: "queue without a gate",
			entries: map[string]string{
				"UNDOLOG_PROXY_MAX_ENGINE_RPCS":      "",
				"UNDOLOG_PROXY_ENGINE_QUEUE_MAX":     "4",
				"UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS": "100",
			},
			wantVar: "UNDOLOG_PROXY_MAX_ENGINE_RPCS",
		},
		{
			name:    "breaker threshold without reset",
			entries: map[string]string{"UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD": "3"},
			wantVar: "UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS",
		},
		{
			name:    "breaker reset without threshold",
			entries: map[string]string{"UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS": "30"},
			wantVar: "UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD",
		},
		{
			name:    "malformed gate cap",
			entries: map[string]string{"UNDOLOG_PROXY_MAX_ENGINE_RPCS": "many"},
			wantVar: "UNDOLOG_PROXY_MAX_ENGINE_RPCS",
		},
		{
			name:    "negative wait budget",
			entries: map[string]string{"UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS": "-5"},
			wantVar: "UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS",
		},
		{
			name:    "negative breaker reset",
			entries: map[string]string{"UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS": "-1"},
			wantVar: "UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			for key, value := range tc.entries {
				t.Setenv(key, value)
			}

			_, err := LoadConfig()
			if err == nil {
				t.Fatal("expected a startup error")
			}
			if !strings.Contains(err.Error(), tc.wantVar) {
				t.Fatalf("error %v must name %s", err, tc.wantVar)
			}
		})
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

// TestNewServerRejectsUnknownPlugin verifies an unknown plugin name stops
// server construction, so a misconfigured UNDOLOG_PROXY_PLUGINS fails at
// startup with the valid options instead of surprising the first request.
func TestNewServerRejectsUnknownPlugin(t *testing.T) {
	cfg := Config{
		ListenAddr:      ":0",
		EngineGRPCAddr:  "engine:50051",
		UpstreamToolURL: "http://upstream",
		TrustedAPIKeys:  map[string]string{"key-1": "org-1"},
		Plugins:         []string{"mystery"},
	}
	_, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), nil)
	if err == nil {
		t.Fatal("an unknown plugin name must fail server construction")
	}
	for _, want := range []string{"mystery", "header"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %v must contain %q", err, want)
		}
	}
}

// TestPluginRejectionWriterEnvelope verifies a plugin rejection answers with
// the proxy's standard JSON error shape, correlated to the request ID on the
// context.
func TestPluginRejectionWriterEnvelope(t *testing.T) {
	rec := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", nil)
	r = r.WithContext(context.WithValue(r.Context(), ctxKeyRequestID, "req-1"))

	pluginRejectionWriter(nil)(rec, r, http.StatusForbidden, "source not allowed")

	if rec.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", rec.Code)
	}
	var body struct {
		RequestID string    `json:"request_id"`
		Code      string    `json:"code"`
		Message   string    `json:"message"`
		Timestamp time.Time `json:"timestamp"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode envelope: %v", err)
	}
	if body.Code != "plugin_rejected" || body.Message != "source not allowed" || body.RequestID != "req-1" || body.Timestamp.IsZero() {
		t.Fatalf("envelope = %+v, want plugin_rejected with reason and request id", body)
	}
}

// TestRateLimitRejectsOverBurstAndCountsMetrics verifies the mounted limiter
// admits a request within the bucket, answers the next one with 429 and a
// Retry-After header, and counts the rejection on /metrics.
func TestRateLimitRejectsOverBurstAndCountsMetrics(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
		RateLimitRPS:          0.0001,
		RateLimitBurst:        1,
	}
	registry := metrics.NewRegistry()
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), registry, nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	within := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	within.Header.Set("X-Api-Key", "key-1")
	withinRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(withinRec, within)
	if withinRec.Code != http.StatusOK {
		t.Fatalf("request within the bucket: status = %d, want 200 (%s)", withinRec.Code, withinRec.Body.String())
	}

	over := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	over.Header.Set("X-Api-Key", "key-1")
	overRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(overRec, over)
	if overRec.Code != http.StatusTooManyRequests {
		t.Fatalf("request over the burst: status = %d, want 429 (%s)", overRec.Code, overRec.Body.String())
	}
	if got := overRec.Header().Get("Retry-After"); got != "10000" {
		t.Fatalf("Retry-After = %q, want 10000 seconds at 0.0001 rps", got)
	}
	if !strings.Contains(overRec.Body.String(), "rate limit exceeded") {
		t.Fatalf("body must carry the rejection reason: %s", overRec.Body.String())
	}

	metricsRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(metricsRec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(metricsRec.Body.String(), "# TYPE undolog_proxy_rate_limit_rejections_total counter") {
		t.Fatalf("rejection counter missing from /metrics:\n%s", metricsRec.Body.String())
	}
}

// TestRateLimitDisabledLeavesRequestsUntouched verifies the default config
// mounts no limiter: with both settings unset, repeated requests behave
// exactly as before rate limiting existed.
func TestRateLimitDisabledLeavesRequestsUntouched(t *testing.T) {
	cfg := Config{
		ListenAddr:            ":0",
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

	for i := 1; i <= 3; i++ {
		req := httptest.NewRequest(http.MethodGet, "/approvals", nil)
		req.Header.Set("X-Api-Key", "key-1")
		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("request %d: status = %d, want 200 with the limiter disabled", i, rec.Code)
		}
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

// TestHandlerRoutesInterceptOutcomesThroughEngineTransport routes every
// engine decision through the real gRPC codec: the scripted engine runs on
// an in-memory listener, so proto marshaling and gRPC status mapping are
// exercised instead of bypassed by an in-process mock.
func TestHandlerRoutesInterceptOutcomesThroughEngineTransport(t *testing.T) {
	t.Run("execute", func(t *testing.T) {
		script := &scriptedEngine{interceptResp: &pb.InterceptResponse{
			Outcome: &pb.InterceptResponse_Execute{Execute: &pb.ExecuteOutcome{EffectId: "eff-1"}},
		}}
		harness := newEngineHarness(t, script)
		execCalls := 0
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
			execCalls++
			if call.ToolName != "search" {
				t.Errorf("executed tool = %q, want search", call.ToolName)
			}
			return protocol.ToolResult{Success: true, Output: json.RawMessage(`{"ok":true}`)}, nil
		}))

		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200 (%s)", rec.Code, rec.Body.String())
		}
		if execCalls != 1 {
			t.Fatalf("executor calls = %d, want 1", execCalls)
		}
		wire := script.interceptReq.GetToolCall()
		if wire.GetOrgId() != "org-1" || wire.GetSessionId() != "s1" || wire.GetToolName() != "search" {
			t.Fatalf("engine received %+v, want the marshaled tool call", wire)
		}
		if got := string(wire.GetArgs()); got != "{}" {
			t.Fatalf("engine received args %q, want the marshaled JSON", got)
		}
		wantSig, err := CanonicalSignature("search", json.RawMessage("{}"))
		if err != nil {
			t.Fatalf("canonical signature: %v", err)
		}
		if wire.GetSignature() != wantSig {
			t.Fatalf("engine received signature %q, want %q", wire.GetSignature(), wantSig)
		}
		if script.commitReq.GetEffectId() != "eff-1" || script.commitReq.GetOrgId() != "org-1" {
			t.Fatalf("commit crossed the wire as %+v", script.commitReq)
		}
		if !script.commitReq.GetResult().GetSuccess() {
			t.Fatalf("commit must carry the executed result, got %+v", script.commitReq.GetResult())
		}
		body := rec.Body.String()
		if !strings.Contains(body, `"status":"executed"`) || !strings.Contains(body, `"effect_id":"eff-1"`) {
			t.Fatalf("response must carry the executed result, got %s", body)
		}
	})

	t.Run("replay", func(t *testing.T) {
		script := &scriptedEngine{interceptResp: &pb.InterceptResponse{
			Outcome: &pb.InterceptResponse_Replay{Replay: &pb.ReplayOutcome{
				EffectId:     "eff-2",
				CachedResult: &pb.ToolResult{Success: true, Output: []byte(`{"cached":true}`)},
			}},
		}}
		harness := newEngineHarness(t, script)
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
			t.Fatal("executor must not run for a replay")
			return protocol.ToolResult{}, nil
		}))

		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())

		if rec.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200 (%s)", rec.Code, rec.Body.String())
		}
		if script.commitReq != nil {
			t.Fatalf("a replay must not commit, got %+v", script.commitReq)
		}
		body := rec.Body.String()
		if !strings.Contains(body, `"status":"replayed"`) || !strings.Contains(body, `"cached":true`) {
			t.Fatalf("response must carry the cached result, got %s", body)
		}
	})

	t.Run("approval", func(t *testing.T) {
		script := &scriptedEngine{interceptResp: &pb.InterceptResponse{
			Outcome: &pb.InterceptResponse_AwaitingApproval{AwaitingApproval: &pb.AwaitingApprovalOutcome{
				EffectId:   "eff-3",
				ApprovalId: "app-1",
			}},
		}}
		harness := newEngineHarness(t, script)
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
			t.Fatal("executor must not run before approval")
			return protocol.ToolResult{}, nil
		}))

		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())

		if rec.Code != http.StatusAccepted {
			t.Fatalf("status = %d, want 202 (%s)", rec.Code, rec.Body.String())
		}
		if script.commitReq != nil {
			t.Fatalf("a pending approval must not commit, got %+v", script.commitReq)
		}
		body := rec.Body.String()
		if !strings.Contains(body, `"status":"pending_approval"`) || !strings.Contains(body, `"approval_id":"app-1"`) {
			t.Fatalf("response must carry the approval id, got %s", body)
		}
	})

	t.Run("approval decision resumes over the wire", func(t *testing.T) {
		script := &scriptedEngine{
			interceptResp: &pb.InterceptResponse{
				Outcome: &pb.InterceptResponse_AwaitingApproval{AwaitingApproval: &pb.AwaitingApprovalOutcome{
					EffectId:   "eff-3",
					ApprovalId: "app-1",
				}},
			},
			approveResp: &pb.ApproveResponse{
				EffectId:  "eff-3",
				SessionId: "s1",
				ToolName:  "delete_user",
				Args:      []byte(`{"id":"u1"}`),
			},
		}
		harness := newEngineHarness(t, script)
		var executed []protocol.ToolCall
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
			executed = append(executed, call)
			return protocol.ToolResult{Success: true}, nil
		}))

		intercept := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(intercept, newToolCallRequest())
		if intercept.Code != http.StatusAccepted {
			t.Fatalf("intercept status = %d, want 202 (%s)", intercept.Code, intercept.Body.String())
		}

		decision := httptest.NewRequest(http.MethodPost, "/approvals/app-1/approve", bytes.NewBufferString(`{"actor":"alice"}`))
		decision.Header.Set("X-Api-Key", "key-1")
		decisionRec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(decisionRec, decision)

		if decisionRec.Code != http.StatusOK {
			t.Fatalf("decision status = %d, want 200 (%s)", decisionRec.Code, decisionRec.Body.String())
		}
		if script.approveReq.GetApprovalId() != "app-1" || script.approveReq.GetOrgId() != "org-1" || script.approveReq.GetActor() != "alice" {
			t.Fatalf("engine received %+v, want the marshaled approval decision", script.approveReq)
		}
		if script.commitReq.GetEffectId() != "eff-3" || !script.commitReq.GetResult().GetSuccess() {
			t.Fatalf("the approved execution must commit over the wire, got %+v", script.commitReq)
		}
		if len(executed) != 1 || executed[0].ToolName != "delete_user" || executed[0].OrgID != "org-1" {
			t.Fatalf("approved execution = %+v, want one org-1 delete_user call", executed)
		}
		body := decisionRec.Body.String()
		if !strings.Contains(body, `"status":"approved"`) || !strings.Contains(body, `"execution":"committed"`) {
			t.Fatalf("decision response must report the committed execution, got %s", body)
		}
	})

	t.Run("unknown outcome answers 500", func(t *testing.T) {
		script := &scriptedEngine{}
		harness := newEngineHarness(t, script)
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
			t.Fatal("executor must not run on an unknown outcome")
			return protocol.ToolResult{}, nil
		}))

		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())

		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("status = %d, want 500 (%s)", rec.Code, rec.Body.String())
		}
		body := rec.Body.String()
		if !strings.Contains(body, `"code":"intercept_failed"`) || !strings.Contains(body, "unexpected intercept outcome") {
			t.Fatalf("response must name the unknown outcome, got %s", body)
		}
	})

	t.Run("intercept failure answers 502", func(t *testing.T) {
		script := &scriptedEngine{interceptErr: status.Error(codes.Internal, "engine exploded")}
		harness := newEngineHarness(t, script)
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
			t.Fatal("executor must not run when the intercept fails")
			return protocol.ToolResult{}, nil
		}))

		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())

		if rec.Code != http.StatusBadGateway {
			t.Fatalf("status = %d, want 502 (%s)", rec.Code, rec.Body.String())
		}
		body := rec.Body.String()
		if !strings.Contains(body, `"code":"intercept_failed"`) || !strings.Contains(body, "engine exploded") {
			t.Fatalf("response must carry the gRPC status message, got %s", body)
		}
	})

	t.Run("commit failure answers 502", func(t *testing.T) {
		script := &scriptedEngine{
			interceptResp: &pb.InterceptResponse{
				Outcome: &pb.InterceptResponse_Execute{Execute: &pb.ExecuteOutcome{EffectId: "eff-1"}},
			},
			commitErr: status.Error(codes.Internal, "commit rejected"),
		}
		harness := newEngineHarness(t, script)
		server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
			return protocol.ToolResult{Success: true}, nil
		}))

		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())

		if rec.Code != http.StatusBadGateway {
			t.Fatalf("status = %d, want 502 (%s)", rec.Code, rec.Body.String())
		}
		body := rec.Body.String()
		if !strings.Contains(body, `"code":"commit_failed"`) || !strings.Contains(body, "commit rejected") {
			t.Fatalf("response must carry the gRPC status message, got %s", body)
		}
	})
}

// TestApprovalsRoutesRejectWrongMethods verifies the approvals endpoints
// answer 405 on a method mismatch instead of running their handlers.
func TestApprovalsRoutesRejectWrongMethods(t *testing.T) {
	server := newProxyTestServer(t, &mockEngineClient{}, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}))

	for _, tc := range []struct {
		method string
		path   string
	}{
		{http.MethodPost, "/approvals"},
		{http.MethodGet, "/approvals/app-1/approve"},
		{http.MethodGet, "/approvals/app-1/reject"},
	} {
		req := httptest.NewRequest(tc.method, tc.path, nil)
		req.Header.Set("X-Api-Key", "key-1")
		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("%s %s: status = %d, want 405", tc.method, tc.path, rec.Code)
		}
	}
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

// TestHandlerInterceptAuthRejectionReports502 verifies a token mismatch stays
// diagnosable end to end: the engine's UNAUTHENTICATED answer passes through
// the real engine client, which names the settings that fix it, and the
// handler surfaces it as a 502 whose body carries that message.
func TestHandlerInterceptAuthRejectionReports502(t *testing.T) {
	engineClient := eng.NewClientWithTransport("unused", eng.RetryConfig{MaxAttempts: 1}, &mockEngineClient{
		interceptErr: status.Error(codes.Unauthenticated, "missing or invalid credentials: expected `authorization` metadata as `Bearer <token>`"),
	}, nil)
	h := NewHandler(engineClient, nil, approval.NewStore(), sse.NewBroadcaster(8), time.Second, 1<<20, nil)

	body := `{"session_id":"sess-1","tool_name":"search","args":{}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	req = req.WithContext(context.WithValue(req.Context(), ctxKeyOrgID, "org-1"))
	rec := httptest.NewRecorder()

	h.ServeHTTP(rec, req)

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("engine auth rejection must return 502, got %d (%s)", rec.Code, rec.Body.String())
	}
	for _, want := range []string{"UNDOLOG_PROXY_ENGINE_AUTH_TOKEN", "authorization"} {
		if !strings.Contains(rec.Body.String(), want) {
			t.Errorf("502 body %q must mention %s", rec.Body.String(), want)
		}
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

// trackingEngine is an engine client that records how many Intercept calls
// are in flight at once, optionally holds each call until the test releases
// it, and adds artificial latency so a burst overlaps. It keeps no shared
// state beyond atomics, so concurrent tool calls cannot race in the harness.
type trackingEngine struct {
	release  chan struct{}
	entered  chan struct{}
	delay    time.Duration
	failure  error
	inFlight atomic.Int32
	maxSeen  atomic.Int32
	calls    atomic.Int32
}

func (e *trackingEngine) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	cur := e.inFlight.Add(1)
	defer e.inFlight.Add(-1)
	for {
		best := e.maxSeen.Load()
		if cur <= best || e.maxSeen.CompareAndSwap(best, cur) {
			break
		}
	}
	e.calls.Add(1)
	select {
	case e.entered <- struct{}{}:
	default:
	}
	if e.release != nil {
		<-e.release
	} else if e.delay > 0 {
		time.Sleep(e.delay)
	}
	if e.failure != nil {
		return protocol.InterceptResponse{}, e.failure
	}
	return protocol.InterceptResponse{Outcome: protocol.InterceptExecute, EffectID: "effect-1"}, nil
}

func (e *trackingEngine) Commit(ctx context.Context, req protocol.CommitRequest) error { return nil }

func (e *trackingEngine) Fail(ctx context.Context, req protocol.FailRequest) error { return nil }

func (e *trackingEngine) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	return protocol.ApproveResponse{}, nil
}

func (e *trackingEngine) Reject(ctx context.Context, req protocol.RejectRequest) error { return nil }

func (e *trackingEngine) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	return protocol.ListPendingApprovalsResponse{}, nil
}

func (e *trackingEngine) Close() error { return nil }

// backpressureKeys are the five engine backpressure settings, all optional.
var backpressureKeys = []string{
	"UNDOLOG_PROXY_MAX_ENGINE_RPCS",
	"UNDOLOG_PROXY_ENGINE_QUEUE_MAX",
	"UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS",
	"UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD",
	"UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS",
}

// newBackpressureServer builds a server around the given engine client the
// way cmd/proxy/main.go wires production, except the engine client is
// injected.
func newBackpressureServer(t *testing.T, engineClient protocol.EngineClient, registry *metrics.Registry) *Server {
	t.Helper()
	cfg := Config{
		ListenAddr:            ":0",
		RequestTimeout:        30 * time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}
	server, err := NewServer(cfg, engineClient, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), registry, nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	return server
}

// newToolCallRequest builds an authenticated tool-call request.
func newToolCallRequest() *http.Request {
	req := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(`{"session_id":"s1","tool_name":"search","args":{}}`))
	req.Header.Set("X-Api-Key", "key-1")
	return req
}

// TestEngineBackpressureGateAnswers503WithoutEngine verifies the N+1st
// in-flight tool call is answered 503 with Retry-After and never reaches
// the engine while the gate is full.
func TestEngineBackpressureGateAnswers503WithoutEngine(t *testing.T) {
	te := &trackingEngine{release: make(chan struct{}), entered: make(chan struct{}, 8)}
	engineClient := eng.NewClientWithTransport("unused", eng.RetryConfig{MaxAttempts: 1}, te, nil, eng.WithBackpressure(eng.BackpressureConfig{
		MaxInFlight: 1,
	}))
	server := newBackpressureServer(t, engineClient, metrics.NewRegistry())

	firstDone := make(chan int, 1)
	go func() {
		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())
		firstDone <- rec.Code
	}()
	select {
	case <-te.entered:
	case <-time.After(2 * time.Second):
		t.Fatal("the first call never reached the engine")
	}

	overRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(overRec, newToolCallRequest())
	if overRec.Code != http.StatusServiceUnavailable {
		t.Fatalf("call at the cap: status = %d, want 503 (%s)", overRec.Code, overRec.Body.String())
	}
	if got := overRec.Header().Get("Retry-After"); got != protocol.BusyRetryAfter {
		t.Fatalf("Retry-After = %q, want %q", got, protocol.BusyRetryAfter)
	}
	if got := te.calls.Load(); got != 1 {
		t.Fatalf("rejected call must not touch the engine, calls = %d", got)
	}

	close(te.release)
	select {
	case code := <-firstDone:
		if code != http.StatusOK {
			t.Fatalf("held call must complete once the slot frees, status = %d", code)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the held call never completed")
	}
}

// TestEngineBackpressureBurstProducesOnly200And503 verifies a burst against
// a low gate produces only 200s and 503s with Retry-After, never a
// transport error, and the observed in-flight count never exceeds the cap,
// which is the structural reason the engine pool cannot exhaust.
func TestEngineBackpressureBurstProducesOnly200And503(t *testing.T) {
	te := &trackingEngine{delay: 100 * time.Millisecond}
	engineClient := eng.NewClientWithTransport("unused", eng.RetryConfig{MaxAttempts: 1}, te, nil, eng.WithBackpressure(eng.BackpressureConfig{
		MaxInFlight: 1,
	}))
	server := newBackpressureServer(t, engineClient, metrics.NewRegistry())

	const callers = 8
	var statuses [callers]int
	var retryAfters [callers]string
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			rec := httptest.NewRecorder()
			<-start
			server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())
			statuses[i] = rec.Code
			retryAfters[i] = rec.Header().Get("Retry-After")
		}(i)
	}
	close(start)
	wg.Wait()

	if observed := int(te.maxSeen.Load()); observed > 1 {
		t.Fatalf("observed in-flight engine RPCs = %d, cap is 1", observed)
	}
	var ok, busy int
	for i, code := range statuses {
		switch code {
		case http.StatusOK:
			ok++
		case http.StatusServiceUnavailable:
			busy++
			if retryAfters[i] != protocol.BusyRetryAfter {
				t.Errorf("call %d: 503 without Retry-After %q", i, retryAfters[i])
			}
		default:
			t.Errorf("call %d: status = %d, want only 200 or 503", i, code)
		}
	}
	if ok == 0 {
		t.Fatal("the first call must win a slot and complete")
	}
	if busy == 0 {
		t.Fatal("a burst against a full gate must reject at least one call with 503")
	}
}

// TestEngineOverloadAnswers503Not502 verifies the engine's own
// RESOURCE_EXHAUSTED answer reaches clients as retryable capacity instead
// of the generic bad gateway error.
func TestEngineOverloadAnswers503Not502(t *testing.T) {
	engineClient := eng.NewClientWithTransport("unused", eng.RetryConfig{MaxAttempts: 1}, &mockEngineClient{
		interceptErr: status.Error(codes.ResourceExhausted, "engine saturated"),
	}, nil)
	server := newBackpressureServer(t, engineClient, metrics.NewRegistry())

	rec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("engine overload: status = %d, want 503 (%s)", rec.Code, rec.Body.String())
	}
	if got := rec.Header().Get("Retry-After"); got != protocol.BusyRetryAfter {
		t.Fatalf("Retry-After = %q, want %q", got, protocol.BusyRetryAfter)
	}
}

// TestEngineBreakerOpenAnswers503OnToolPath verifies the engine's own
// failure stays a 502, and once it opens the breaker the next tool call is
// answered 503 with Retry-After without touching the engine.
func TestEngineBreakerOpenAnswers503OnToolPath(t *testing.T) {
	te := &trackingEngine{failure: status.Error(codes.Unavailable, "engine down")}
	engineClient := eng.NewClientWithTransport("unused", eng.RetryConfig{MaxAttempts: 1}, te, nil, eng.WithBackpressure(eng.BackpressureConfig{
		BreakerThreshold: 1,
		BreakerReset:     30 * time.Second,
	}))
	server := newBackpressureServer(t, engineClient, metrics.NewRegistry())

	firstRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(firstRec, newToolCallRequest())
	if firstRec.Code != http.StatusBadGateway {
		t.Fatalf("engine failure before the breaker opens: status = %d, want 502", firstRec.Code)
	}

	secondRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(secondRec, newToolCallRequest())
	if secondRec.Code != http.StatusServiceUnavailable {
		t.Fatalf("call against an open breaker: status = %d, want 503 (%s)", secondRec.Code, secondRec.Body.String())
	}
	if got := secondRec.Header().Get("Retry-After"); got != protocol.BusyRetryAfter {
		t.Fatalf("Retry-After = %q, want %q", got, protocol.BusyRetryAfter)
	}
	if got := te.calls.Load(); got != 1 {
		t.Fatalf("rejected call must not touch the engine, calls = %d", got)
	}
}

// TestEngineBackpressureDisabledByDefault verifies the shipped defaults run
// no queue, no gate, and no breaker: with every setting unset the tool path
// behaves exactly as it did before backpressure existed and no guard metric
// appears on /metrics.
func TestEngineBackpressureDisabledByDefault(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "key-1=org-1")
	t.Setenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", "http://upstream")
	for _, key := range backpressureKeys {
		t.Setenv(key, "")
	}

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.MaxEngineRPCs != 0 || cfg.EngineQueueMax != 0 || cfg.EngineQueueWait != 0 ||
		cfg.EngineBreakerThreshold != 0 || cfg.EngineBreakerReset != 0 {
		t.Fatalf("backpressure must be disabled by default, got %+v", cfg)
	}

	te := &trackingEngine{delay: time.Millisecond}
	engineClient := eng.NewClientWithTransport(cfg.EngineGRPCAddr, eng.RetryConfig{MaxAttempts: 1}, te, nil, eng.WithBackpressure(eng.BackpressureConfig{
		MaxInFlight:      cfg.MaxEngineRPCs,
		QueueMax:         cfg.EngineQueueMax,
		QueueWait:        cfg.EngineQueueWait,
		BreakerThreshold: cfg.EngineBreakerThreshold,
		BreakerReset:     cfg.EngineBreakerReset,
	}))
	registry := metrics.NewRegistry()
	engineClient.SetMetrics(registry)
	server, err := NewServer(cfg, engineClient, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), registry, nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	for i := 1; i <= 3; i++ {
		rec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(rec, newToolCallRequest())
		if rec.Code != http.StatusOK {
			t.Fatalf("call %d with the guard disabled: status = %d (%s)", i, rec.Code, rec.Body.String())
		}
	}
	if got := te.calls.Load(); got != 3 {
		t.Fatalf("engine calls = %d, want 3", got)
	}
	if out := registry.Render(); strings.Contains(out, "undolog_proxy_engine_inflight") {
		t.Fatalf("disabled backpressure must register no guard metrics:\n%s", out)
	}
}
