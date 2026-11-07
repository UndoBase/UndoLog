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
	listResp     protocol.ListDeadLettersResponse
	listErr      error
	lastReq      protocol.ListDeadLettersRequest
	retryResp    protocol.RetryDeadLetterResponse
	retryErr     error
	lastRetryReq protocol.RetryDeadLetterRequest
	skipResp     protocol.SkipDeadLetterResponse
	skipErr      error
	lastSkipReq  protocol.SkipDeadLetterRequest
}

func (m *mockDLQEngine) ListDeadLetters(ctx context.Context, req protocol.ListDeadLettersRequest) (protocol.ListDeadLettersResponse, error) {
	m.lastReq = req
	return m.listResp, m.listErr
}

func (m *mockDLQEngine) RetryDeadLetter(ctx context.Context, req protocol.RetryDeadLetterRequest) (protocol.RetryDeadLetterResponse, error) {
	m.lastRetryReq = req
	return m.retryResp, m.retryErr
}

func (m *mockDLQEngine) SkipDeadLetter(ctx context.Context, req protocol.SkipDeadLetterRequest) (protocol.SkipDeadLetterResponse, error) {
	m.lastSkipReq = req
	return m.skipResp, m.skipErr
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

func TestAdminDLQ_RetrySuccessAndAudit(t *testing.T) {
	validID := "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
	mockEng := &mockDLQEngine{
		retryResp: protocol.RetryDeadLetterResponse{
			Record: protocol.DeadLetterRecord{
				DeadLetterID:        validID,
				OrgID:               "org-1",
				SessionID:           "c3b9b4a1-8d2b-4d7a-8f91-7d3a2b4c5e6f",
				EffectID:            "d1e2f3a4-b5c6-4d7e-8f90-1a2b3c4d5e6f",
				UndoID:              "e2f3a4b5-c6d7-4e8f-901a-2b3c4d5e6f70",
				CompensationFn:      "cancel_reservation",
				CompensationVersion: "1.0.0",
				CompensationArgs:    json.RawMessage(`{"reservation_id":"res-12345"}`),
				ErrorMessage:        "temporary timeout",
				RetryCount:          1,
				State:               "retrying",
				CreatedAt:           "2026-11-07T12:00:00Z",
				UpdatedAt:           "2026-11-07T12:05:00Z",
			},
		},
	}

	var logBuf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&logBuf, &slog.HandlerOptions{Level: slog.LevelInfo}))

	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		logger,
	)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/retry", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d (body: %s)", w.Code, w.Body.String())
	}

	if mockEng.lastRetryReq.OrgID != "org-1" {
		t.Errorf("expected engine org_id org-1, got %q", mockEng.lastRetryReq.OrgID)
	}
	if mockEng.lastRetryReq.DeadLetterID != validID {
		t.Errorf("expected engine dead_letter_id %s, got %q", validID, mockEng.lastRetryReq.DeadLetterID)
	}

	var rec protocol.DeadLetterRecord
	if err := json.Unmarshal(w.Body.Bytes(), &rec); err != nil {
		t.Fatalf("unmarshal response failed: %v", err)
	}
	if rec.DeadLetterID != validID || rec.State != "retrying" || rec.RetryCount != 1 {
		t.Errorf("unexpected record content: %+v", rec)
	}

	logOutput := logBuf.String()
	if !strings.Contains(logOutput, "admin dead letter action") {
		t.Errorf("expected audit action log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"action":"retry"`) {
		t.Errorf("expected action:retry in log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"dead_letter_id":"`+validID+`"`) {
		t.Errorf("expected dead_letter_id in log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"org_id":"org-1"`) {
		t.Errorf("expected org_id:org-1 in log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"outcome":"retrying"`) {
		t.Errorf("expected outcome:retrying in log, got:\n%s", logOutput)
	}
}

func TestAdminDLQ_SkipSuccessAndAudit(t *testing.T) {
	validID := "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
	mockEng := &mockDLQEngine{
		skipResp: protocol.SkipDeadLetterResponse{
			Record: protocol.DeadLetterRecord{
				DeadLetterID:        validID,
				OrgID:               "org-1",
				SessionID:           "c3b9b4a1-8d2b-4d7a-8f91-7d3a2b4c5e6f",
				EffectID:            "d1e2f3a4-b5c6-4d7e-8f90-1a2b3c4d5e6f",
				UndoID:              "e2f3a4b5-c6d7-4e8f-901a-2b3c4d5e6f70",
				CompensationFn:      "cancel_reservation",
				CompensationVersion: "1.0.0",
				CompensationArgs:    json.RawMessage(`{"reservation_id":"res-12345"}`),
				ErrorMessage:        "unrecoverable error",
				RetryCount:          0,
				State:               "skipped",
				CreatedAt:           "2026-11-07T12:00:00Z",
				UpdatedAt:           "2026-11-07T12:05:00Z",
			},
		},
	}

	var logBuf bytes.Buffer
	logger := slog.New(slog.NewJSONHandler(&logBuf, &slog.HandlerOptions{Level: slog.LevelInfo}))

	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		logger,
	)

	req := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/skip", nil)
	req.Header.Set("X-Api-Key", "admin-key")
	w := httptest.NewRecorder()

	server.handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 OK, got %d (body: %s)", w.Code, w.Body.String())
	}

	if mockEng.lastSkipReq.OrgID != "org-1" {
		t.Errorf("expected engine org_id org-1, got %q", mockEng.lastSkipReq.OrgID)
	}
	if mockEng.lastSkipReq.DeadLetterID != validID {
		t.Errorf("expected engine dead_letter_id %s, got %q", validID, mockEng.lastSkipReq.DeadLetterID)
	}

	var rec protocol.DeadLetterRecord
	if err := json.Unmarshal(w.Body.Bytes(), &rec); err != nil {
		t.Fatalf("unmarshal response failed: %v", err)
	}
	if rec.DeadLetterID != validID || rec.State != "skipped" {
		t.Errorf("unexpected record content: %+v", rec)
	}

	logOutput := logBuf.String()
	if !strings.Contains(logOutput, "admin dead letter action") {
		t.Errorf("expected audit action log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"action":"skip"`) {
		t.Errorf("expected action:skip in log, got:\n%s", logOutput)
	}
	if !strings.Contains(logOutput, `"outcome":"skipped"`) {
		t.Errorf("expected outcome:skipped in log, got:\n%s", logOutput)
	}
}

func TestAdminDLQ_NotFoundCases(t *testing.T) {
	validID := "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
	mockEng := &mockDLQEngine{
		retryErr: status.Error(codes.NotFound, "record not found"),
		skipErr:  status.Error(codes.NotFound, "record not in failed state"),
	}

	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	// Retry returns 404
	reqRetry := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/retry", nil)
	reqRetry.Header.Set("X-Api-Key", "admin-key")
	wRetry := httptest.NewRecorder()
	server.handler.ServeHTTP(wRetry, reqRetry)
	if wRetry.Code != http.StatusNotFound {
		t.Errorf("expected 404 for retry not found, got %d", wRetry.Code)
	}
	if !strings.Contains(wRetry.Body.String(), `"code":"dead_letter_not_found"`) {
		t.Errorf("expected code dead_letter_not_found, got %s", wRetry.Body.String())
	}

	// Skip returns 404
	reqSkip := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/skip", nil)
	reqSkip.Header.Set("X-Api-Key", "admin-key")
	wSkip := httptest.NewRecorder()
	server.handler.ServeHTTP(wSkip, reqSkip)
	if wSkip.Code != http.StatusNotFound {
		t.Errorf("expected 404 for skip not found, got %d", wSkip.Code)
	}
	if !strings.Contains(wSkip.Body.String(), `"code":"dead_letter_not_found"`) {
		t.Errorf("expected code dead_letter_not_found, got %s", wSkip.Body.String())
	}
}

func TestAdminDLQ_MalformedUUIDReturns400(t *testing.T) {
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	malformedIDs := []string{
		"not-a-uuid",
		"12345",
		"9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6g",  // invalid hex
		"9b1deb4d3b7d4bad9bdd2b0d7b3dcb6d",      // missing hyphens
		"9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6de", // 37 chars
	}

	for _, id := range malformedIDs {
		// Retry
		reqRetry := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+id+"/retry", nil)
		reqRetry.Header.Set("X-Api-Key", "admin-key")
		wRetry := httptest.NewRecorder()
		server.handler.ServeHTTP(wRetry, reqRetry)
		if wRetry.Code != http.StatusBadRequest {
			t.Errorf("expected 400 for malformed retry UUID %q, got %d", id, wRetry.Code)
		}
		if !strings.Contains(wRetry.Body.String(), `"code":"invalid_argument"`) {
			t.Errorf("expected invalid_argument code, got %s", wRetry.Body.String())
		}

		// Skip
		reqSkip := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+id+"/skip", nil)
		reqSkip.Header.Set("X-Api-Key", "admin-key")
		wSkip := httptest.NewRecorder()
		server.handler.ServeHTTP(wSkip, reqSkip)
		if wSkip.Code != http.StatusBadRequest {
			t.Errorf("expected 400 for malformed skip UUID %q, got %d", id, wSkip.Code)
		}
		if !strings.Contains(wSkip.Body.String(), `"code":"invalid_argument"`) {
			t.Errorf("expected invalid_argument code, got %s", wSkip.Body.String())
		}
	}
}

func TestAdminDLQ_WrongMethodReturns405(t *testing.T) {
	validID := "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	methods := []string{http.MethodGet, http.MethodPut, http.MethodDelete, http.MethodPatch}

	for _, method := range methods {
		// Retry endpoint
		reqRetry := httptest.NewRequest(method, "/api/v1/admin/dead-letters/"+validID+"/retry", nil)
		reqRetry.Header.Set("X-Api-Key", "admin-key")
		wRetry := httptest.NewRecorder()
		server.handler.ServeHTTP(wRetry, reqRetry)
		if wRetry.Code != http.StatusMethodNotAllowed {
			t.Errorf("expected 405 for %s on retry, got %d", method, wRetry.Code)
		}

		// Skip endpoint
		reqSkip := httptest.NewRequest(method, "/api/v1/admin/dead-letters/"+validID+"/skip", nil)
		reqSkip.Header.Set("X-Api-Key", "admin-key")
		wSkip := httptest.NewRecorder()
		server.handler.ServeHTTP(wSkip, reqSkip)
		if wSkip.Code != http.StatusMethodNotAllowed {
			t.Errorf("expected 405 for %s on skip, got %d", method, wSkip.Code)
		}
	}
}

func TestAdminDLQ_MemberKeyReturns403OnDecisions(t *testing.T) {
	validID := "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
	mockEng := &mockDLQEngine{}
	server := newTestAdminServer(t, mockEng,
		map[string]string{"member-key": "org-1"},
		map[string]string{"admin-key": "org-1"},
		nil,
	)

	reqRetry := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/retry", nil)
	reqRetry.Header.Set("X-Api-Key", "member-key")
	wRetry := httptest.NewRecorder()
	server.handler.ServeHTTP(wRetry, reqRetry)
	if wRetry.Code != http.StatusForbidden {
		t.Errorf("expected 403 for member key on retry, got %d", wRetry.Code)
	}

	reqSkip := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/skip", nil)
	reqSkip.Header.Set("X-Api-Key", "member-key")
	wSkip := httptest.NewRecorder()
	server.handler.ServeHTTP(wSkip, reqSkip)
	if wSkip.Code != http.StatusForbidden {
		t.Errorf("expected 403 for member key on skip, got %d", wSkip.Code)
	}
}

func TestAdminDLQ_DecisionEngineErrorMappings(t *testing.T) {
	validID := "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"

	cases := []struct {
		name       string
		engineErr  error
		wantStatus int
		wantCode   string
	}{
		{
			name:       "unimplemented",
			engineErr:  status.Error(codes.Unimplemented, "unimplemented"),
			wantStatus: http.StatusNotImplemented,
			wantCode:   "dead_letter_unavailable",
		},
		{
			name:       "unavailable",
			engineErr:  status.Error(codes.Unavailable, "service unavailable"),
			wantStatus: http.StatusServiceUnavailable,
			wantCode:   "engine_unavailable",
		},
		{
			name:       "internal_error",
			engineErr:  errors.New("db error"),
			wantStatus: http.StatusBadGateway,
			wantCode:   "engine_error",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			mockEng := &mockDLQEngine{
				retryErr: tc.engineErr,
				skipErr:  tc.engineErr,
			}
			server := newTestAdminServer(t, mockEng,
				map[string]string{"member-key": "org-1"},
				map[string]string{"admin-key": "org-1"},
				nil,
			)

			// Test Retry
			reqRetry := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/retry", nil)
			reqRetry.Header.Set("X-Api-Key", "admin-key")
			wRetry := httptest.NewRecorder()
			server.handler.ServeHTTP(wRetry, reqRetry)
			if wRetry.Code != tc.wantStatus {
				t.Errorf("retry %s: expected status %d, got %d", tc.name, tc.wantStatus, wRetry.Code)
			}
			if !strings.Contains(wRetry.Body.String(), tc.wantCode) {
				t.Errorf("retry %s: expected code %s, got %s", tc.name, tc.wantCode, wRetry.Body.String())
			}

			// Test Skip
			reqSkip := httptest.NewRequest(http.MethodPost, "/api/v1/admin/dead-letters/"+validID+"/skip", nil)
			reqSkip.Header.Set("X-Api-Key", "admin-key")
			wSkip := httptest.NewRecorder()
			server.handler.ServeHTTP(wSkip, reqSkip)
			if wSkip.Code != tc.wantStatus {
				t.Errorf("skip %s: expected status %d, got %d", tc.name, tc.wantStatus, wSkip.Code)
			}
			if !strings.Contains(wSkip.Body.String(), tc.wantCode) {
				t.Errorf("skip %s: expected code %s, got %s", tc.name, tc.wantCode, wSkip.Body.String())
			}
		})
	}
}

func TestAdminDLQ_DirectDecisionDelegation(t *testing.T) {
	mockEng := &mockDLQEngine{
		retryResp: protocol.RetryDeadLetterResponse{
			Record: protocol.DeadLetterRecord{State: "retrying"},
		},
		skipResp: protocol.SkipDeadLetterResponse{
			Record: protocol.DeadLetterRecord{State: "skipped"},
		},
	}

	retryResp, err := mockEng.RetryDeadLetter(context.Background(), protocol.RetryDeadLetterRequest{
		OrgID:        "org-1",
		DeadLetterID: "dl-1",
	})
	if err != nil {
		t.Fatalf("RetryDeadLetter failed: %v", err)
	}
	if retryResp.Record.State != "retrying" {
		t.Errorf("expected retrying, got %s", retryResp.Record.State)
	}

	skipResp, err := mockEng.SkipDeadLetter(context.Background(), protocol.SkipDeadLetterRequest{
		OrgID:        "org-1",
		DeadLetterID: "dl-1",
	})
	if err != nil {
		t.Fatalf("SkipDeadLetter failed: %v", err)
	}
	if skipResp.Record.State != "skipped" {
		t.Errorf("expected skipped, got %s", skipResp.Record.State)
	}
}
