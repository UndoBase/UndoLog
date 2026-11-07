// Package proxy tests the administrative dead-letter queue endpoints and API key roles.
package proxy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
)

type mockDLQEngine struct {
	mockEngineClient
	listResp protocol.ListDeadLettersResponse
	listErr  error
	lastReq  protocol.ListDeadLettersRequest
}

func (m *mockDLQEngine) ListDeadLetters(ctx context.Context, req protocol.ListDeadLettersRequest) (protocol.ListDeadLettersResponse, error) {
	m.lastReq = req
	return m.listResp, m.listErr
}

func newTestAdminServer(t *testing.T, engineClient protocol.EngineClient, memberKeys, adminKeys map[string]string, logger *slog.Logger) *Server {
	t.Helper()
	cfg := Config{
		ListenAddr:     ":0",
		TrustedAPIKeys: memberKeys,
		AdminAPIKeys:   adminKeys,
		RequestTimeout: 5 * time.Second,
		LogLevel:       "info",
	}
	server, err := NewServer(cfg, engineClient, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{Success: true}, nil
	}), metrics.NewRegistry(), logger)
	if err != nil {
		t.Fatalf("NewServer failed: %v", err)
	}
	return server
}

func TestAdminConfig_ParsingAndValidation(t *testing.T) {
	dir := t.TempDir()
	tomlPath := filepath.Join(dir, "undolog.toml")
	content := `
listen_addr = ":9090"
[api_keys]
member_secret = "org-1"

[admin_api_keys]
admin_secret = "org-1"
`
	if err := os.WriteFile(tomlPath, []byte(content), 0o600); err != nil {
		t.Fatalf("WriteFile failed: %v", err)
	}

	t.Setenv("UNDOLOG_PROXY_CONFIG", tomlPath)
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "")
	t.Setenv("UNDOLOG_PROXY_ADMIN_API_KEYS", "")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}

	if cfg.TrustedAPIKeys["member_secret"] != "org-1" {
		t.Errorf("expected member_secret=org-1, got %v", cfg.TrustedAPIKeys)
	}
	if cfg.AdminAPIKeys["admin_secret"] != "org-1" {
		t.Errorf("expected admin_secret=org-1, got %v", cfg.AdminAPIKeys)
	}

	// Environment variable override
	t.Setenv("UNDOLOG_PROXY_ADMIN_API_KEYS", "env_admin=org-2")
	cfgEnv, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig with env override failed: %v", err)
	}
	if cfgEnv.AdminAPIKeys["env_admin"] != "org-2" {
		t.Errorf("expected env_admin=org-2, got %v", cfgEnv.AdminAPIKeys)
	}
}

func TestAdminConfig_BlankKeyRejectedInFile(t *testing.T) {
	dir := t.TempDir()
	tomlPath := filepath.Join(dir, "undolog.toml")
	content := `
[api_keys]
key1 = "org-1"

[admin_api_keys]
"" = "org-1"
`
	if err := os.WriteFile(tomlPath, []byte(content), 0o600); err != nil {
		t.Fatalf("WriteFile failed: %v", err)
	}

	t.Setenv("UNDOLOG_PROXY_CONFIG", tomlPath)
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "")
	t.Setenv("UNDOLOG_PROXY_ADMIN_API_KEYS", "")

	_, err := LoadConfig()
	if err == nil {
		t.Fatalf("expected error for blank admin key in config file")
	}
}

func TestAdminConfig_OverlappingKeysRejected(t *testing.T) {
	t.Setenv("UNDOLOG_PROXY_CONFIG", "")
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "shared-key=org-1")
	t.Setenv("UNDOLOG_PROXY_ADMIN_API_KEYS", "shared-key=org-1")

	_, err := LoadConfig()
	if err == nil {
		t.Fatalf("expected startup failure on overlapping api_keys and admin_api_keys")
	}
	if !strings.Contains(err.Error(), "both api_keys and admin_api_keys") {
		t.Errorf("expected error message mentioning overlap, got: %v", err)
	}
}

func TestAdminDLQ_MemberKeyReceives403(t *testing.T) {
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "member-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403 Forbidden for member key on admin route, got %d", w.Code)
	}

	var errBody map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &errBody); err != nil {
		t.Fatalf("unmarshal error body failed: %v", err)
	}
	if errBody["code"] != "auth_failed" {
		t.Errorf("expected code auth_failed, got %v", errBody["code"])
	}
}

func TestAdminDLQ_UnsetAdminKeysDeniesAdminAccess(t *testing.T) {
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		nil, // no admin keys
		nil,
	)

	// Member key attempt
	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "member-key")
	w := httptest.NewRecorder()
	server.handler.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("expected 403 for member key when no admin keys configured, got %d", w.Code)
	}

	// Missing key attempt
	reqUnauth := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	wUnauth := httptest.NewRecorder()
	server.handler.ServeHTTP(wUnauth, reqUnauth)
	if wUnauth.Code != http.StatusUnauthorized {
		t.Errorf("expected 401 for missing key, got %d", wUnauth.Code)
	}
}

func TestAdminDLQ_AdminKeyListsOwnOrgAndLogsAudit(t *testing.T) {
	mockEng := &mockDLQEngine{
		listResp: protocol.ListDeadLettersResponse{
			Records: []protocol.DeadLetterRecord{
				{
					DeadLetterID:        "dl-1",
					OrgID:               "org-1",
					SessionID:           "sess-1",
					EffectID:            "eff-1",
					UndoID:              "undo-1",
					CompensationFn:      "rollback_payment",
					CompensationVersion: "1.0.0",
					CompensationArgs:    json.RawMessage(`{"amount":100}`),
					ErrorMessage:        "network timeout",
					RetryCount:          0,
					State:               "failed",
					CreatedAt:           "2026-11-07T12:00:00Z",
					UpdatedAt:           "2026-11-07T12:00:00Z",
				},
			},
			Count: 1,
		},
	}

	var logBuf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&logBuf, &slog.HandlerOptions{Level: slog.LevelInfo}))

	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		logger,
	)

	// Admin supplies query param org_id="tampered-org"; proxy MUST ignore it and use org-1
	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters?state=failed&org_id=tampered-org", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d (body: %s)", w.Code, w.Body.String())
	}

	// Verify org_id requested from engine was org-1, not tampered-org
	if mockEng.lastReq.OrgID != "org-1" {
		t.Errorf("expected engine request org_id to be org-1, got %q", mockEng.lastReq.OrgID)
	}
	if mockEng.lastReq.State != "failed" {
		t.Errorf("expected engine request state to be failed, got %q", mockEng.lastReq.State)
	}

	var resp protocol.ListDeadLettersResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal response failed: %v", err)
	}
	if resp.Count != 1 || len(resp.Records) != 1 {
		t.Fatalf("expected 1 record, got count=%d, records=%d", resp.Count, len(resp.Records))
	}
	rec := resp.Records[0]
	if rec.DeadLetterID != "dl-1" || rec.State != "failed" || rec.CreatedAt != "2026-11-07T12:00:00Z" {
		t.Errorf("unexpected record contents: %+v", rec)
	}

	// Verify audit log line
	logOutput := logBuf.String()
	if !strings.Contains(logOutput, "admin dead letters listed") {
		t.Errorf("expected audit line 'admin dead letters listed' in logs, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"org_id":"org-1"`) {
		t.Errorf("expected org_id:org-1 in audit log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"entry_count":1`) {
		t.Errorf("expected entry_count:1 in audit log, got:\n%s", logOutput)
	}
}

func TestAdminDLQ_InvalidStateReturns400(t *testing.T) {
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters?state=unrecognized", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 Bad Request, got %d", w.Code)
	}

	var errBody map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &errBody); err != nil {
		t.Fatalf("unmarshal error body failed: %v", err)
	}
	if errBody["code"] != "invalid_argument" {
		t.Errorf("expected code invalid_argument, got %v", errBody["code"])
	}
}

func TestAdminDLQ_DefaultStateIsFailed(t *testing.T) {
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d", w.Code)
	}
	if mockEng.lastReq.State != "failed" {
		t.Errorf("expected default state to be 'failed', got %q", mockEng.lastReq.State)
	}
}

func TestAdminDLQ_EngineUnimplementedMapsTo501(t *testing.T) {
	mockEng := &mockDLQEngine{
		listErr: status.Error(codes.Unimplemented, "dead-letter queue is not supported on SQLite"),
	}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusNotImplemented {
		t.Fatalf("expected 501 Not Implemented, got %d", w.Code)
	}

	var errBody map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &errBody); err != nil {
		t.Fatalf("unmarshal error body failed: %v", err)
	}
	if errBody["code"] != "dead_letter_unavailable" {
		t.Errorf("expected code dead_letter_unavailable, got %v", errBody["code"])
	}
}

func TestAdminDLQ_EngineOverloadMapsTo503(t *testing.T) {
	mockEng := &mockDLQEngine{
		listErr: protocol.ErrEngineBusy,
	}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 Service Unavailable, got %d", w.Code)
	}
	if w.Header().Get("Retry-After") != "1" {
		t.Errorf("expected Retry-After: 1 header, got %q", w.Header().Get("Retry-After"))
	}
}

func TestAdminDLQ_EngineUnavailableMapsTo503(t *testing.T) {
	mockEng := &mockDLQEngine{
		listErr: status.Error(codes.Unavailable, "connection refused"),
	}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 Service Unavailable, got %d", w.Code)
	}
	if w.Header().Get("Retry-After") != "1" {
		t.Errorf("expected Retry-After: 1 header, got %q", w.Header().Get("Retry-After"))
	}
}

func TestAdminDLQ_OtherEngineErrorMapsTo502(t *testing.T) {
	mockEng := &mockDLQEngine{
		listErr: errors.New("database connection broken"),
	}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodGet, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected 502 Bad Gateway, got %d", w.Code)
	}
}

func TestAdminDLQ_MethodNotAllowed(t *testing.T) {
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405 Method Not Allowed, got %d", w.Code)
	}
}

func TestAdminDLQ_TenantRoutesAccessibleByBothRoles(t *testing.T) {
	mockEng := &mockDLQEngine{
		mockEngineClient: mockEngineClient{
			interceptResp: protocol.InterceptResponse{
				Outcome:  protocol.InterceptExecute,
				EffectID: "eff-1",
			},
		},
	}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	body := `{"session_id":"sess-1","tool_name":"search","args":{}}`

	// Member key to tenant route
	reqMember := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	reqMember.Header.Set("X-Api-Key", "member-key")
	wMember := httptest.NewRecorder()
	server.handler.ServeHTTP(wMember, reqMember)
	if wMember.Code != http.StatusOK {
		t.Errorf("expected 200 for member on tenant route, got %d", wMember.Code)
	}

	// Admin key to tenant route
	reqAdmin := httptest.NewRequest(http.MethodPost, "/mcp/tool_call", bytes.NewBufferString(body))
	reqAdmin.Header.Set("X-Api-Key", "admin-key")
	wAdmin := httptest.NewRecorder()
	server.handler.ServeHTTP(wAdmin, reqAdmin)
	if wAdmin.Code != http.StatusOK {
		t.Errorf("expected 200 for admin on tenant route, got %d", wAdmin.Code)
	}
}

func TestAdminDLQ_DirectListDelegation(t *testing.T) {
	mockEng := &mockDLQEngine{
		listResp: protocol.ListDeadLettersResponse{Count: 2},
	}
	resp, err := mockEng.ListDeadLetters(context.Background(), protocol.ListDeadLettersRequest{
		OrgID: "org-1",
		State: "failed",
	})
	if err != nil {
		t.Fatalf("ListDeadLetters failed: %v", err)
	}
	if resp.Count != 2 {
		t.Errorf("expected count 2, got %d", resp.Count)
	}
}
