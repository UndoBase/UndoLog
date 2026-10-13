// Package proxy tests the authentication middleware's digest identity: the
// constant-time-matched API key digest reaches the handler context for the
// rate limiter to meter, and the raw key never enters it.
package proxy

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"undolog-proxy/internal/plugin"
	"undolog-proxy/internal/protocol"
)

// TestAuthMiddlewareStoresKeyDigest verifies an authenticated request carries
// the matched key's digest to the handler, that the identity is not the raw
// key, and that rejected requests carry no identity at all.
func TestAuthMiddlewareStoresKeyDigest(t *testing.T) {
	stack := NewMiddlewareStack(nil, map[string]string{"key-1": "org-1"})
	var seen string
	handler := stack.Auth(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		seen = plugin.APIKeyDigestFrom(r.Context())
	}))

	authenticated := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	authenticated.Header.Set("X-Api-Key", "key-1")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, authenticated)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if seen == "" {
		t.Fatal("authenticated request must carry a key digest")
	}
	if seen == "key-1" {
		t.Fatal("the raw API key must not enter the context")
	}
	if len(seen) != 64 {
		t.Fatalf("digest length = %d, want 64 hex characters", len(seen))
	}

	seen = ""
	unknown := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	unknown.Header.Set("X-Api-Key", "not-a-real-key")
	handler.ServeHTTP(httptest.NewRecorder(), unknown)
	if seen != "" {
		t.Fatal("rejected request must carry no digest")
	}

	missing := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	handler.ServeHTTP(httptest.NewRecorder(), missing)
	if seen != "" {
		t.Fatal("unauthenticated request must carry no digest")
	}
}

// TestRouterAuthGatesProtectedRoutes verifies the assembled router rejects
// every protected route with 401 for a missing key and 403 for an unknown
// key, while /health stays open to unauthenticated callers.
func TestRouterAuthGatesProtectedRoutes(t *testing.T) {
	server := newProxyTestServer(t, &mockEngineClient{}, ToolExecutorFunc(func(_ context.Context, _ protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}))

	for _, path := range []string{"/mcp/tool_call", "/events", "/approvals", "/approvals/app-1/approve"} {
		missing := httptest.NewRequest(http.MethodGet, path, nil)
		missingRec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(missingRec, missing)
		if missingRec.Code != http.StatusUnauthorized {
			t.Errorf("%s without a key: status = %d, want 401", path, missingRec.Code)
		}

		unknown := httptest.NewRequest(http.MethodGet, path, nil)
		unknown.Header.Set("X-Api-Key", "not-a-real-key")
		unknownRec := httptest.NewRecorder()
		server.httpSrv.Handler.ServeHTTP(unknownRec, unknown)
		if unknownRec.Code != http.StatusForbidden {
			t.Errorf("%s with an unknown key: status = %d, want 403", path, unknownRec.Code)
		}
	}

	health := httptest.NewRequest(http.MethodGet, "/health", nil)
	healthRec := httptest.NewRecorder()
	server.httpSrv.Handler.ServeHTTP(healthRec, health)
	if healthRec.Code != http.StatusOK {
		t.Fatalf("/health without a key: status = %d, want 200", healthRec.Code)
	}
}
