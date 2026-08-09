// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// The middleware chain resolves organization identity from API keys, assigns
// request IDs, writes structured logs, and converts panics into JSON errors.
package proxy

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"undolog-proxy/internal/metrics"
)

type ctxKey string

const (
	ctxKeyOrgID     ctxKey = "org_id"
	ctxKeyRequestID ctxKey = "request_id"

	httpRequestsMetric = "undolog_proxy_http_requests_total"
	httpDurationMetric = "undolog_proxy_http_request_duration_seconds"
)

// apiKeyEntry holds one trusted API key as its SHA-256 digest so the request
// path never compares plaintext keys. Constant-time digest comparison removes
// the timing side channel a naive string membership check would expose.
//
// SHA-256 is deliberate: a slow password KDF such as bcrypt only helps
// low-entropy passwords and would cost CPU time on every request.
type apiKeyEntry struct {
	digest [sha256.Size]byte
	orgID  string
}

// MiddlewareStack composes the proxy middlewares into one reusable bundle.
type MiddlewareStack struct {
	logger  *slog.Logger
	apiKeys []apiKeyEntry
	metrics *metrics.Registry
}

// NewMiddlewareStack builds the middleware chain used by the proxy server.
func NewMiddlewareStack(logger *slog.Logger, trustedAPIKeys map[string]string) *MiddlewareStack {
	if logger == nil {
		logger = slog.Default()
	}
	entries := make([]apiKeyEntry, 0, len(trustedAPIKeys))
	for key, org := range trustedAPIKeys {
		entries = append(entries, apiKeyEntry{digest: sha256.Sum256([]byte(key)), orgID: org})
	}
	return &MiddlewareStack{logger: logger, apiKeys: entries}
}

// SetMetrics wires the registry so every processed request contributes an HTTP
// request counter and a duration histogram. A nil registry disables it.
func (m *MiddlewareStack) SetMetrics(reg *metrics.Registry) {
	m.metrics = reg
}

// Auth resolves X-Api-Key to an organization ID and stores it on the request.
func (m *MiddlewareStack) Auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		apiKey := strings.TrimSpace(r.Header.Get("X-Api-Key"))
		if apiKey == "" {
			writeError(w, http.StatusUnauthorized, "auth_failed", "X-Api-Key header required", "")
			return
		}
		orgID, ok := m.orgForKey(sha256.Sum256([]byte(apiKey)))
		if !ok {
			writeError(w, http.StatusForbidden, "auth_failed", "API key not recognized", "")
			return
		}
		ctx := context.WithValue(r.Context(), ctxKeyOrgID, orgID)
		r = r.WithContext(ctx)
		r.Header.Set("X-Org-Id", orgID)
		next.ServeHTTP(w, r)
	})
}

// orgForKey resolves the organization served by an API-key digest. Every
// candidate digest is compared in constant time so the lookup does not leak
// which key (or whether any key) matched.
func (m *MiddlewareStack) orgForKey(digest [sha256.Size]byte) (string, bool) {
	for _, entry := range m.apiKeys {
		if subtle.ConstantTimeCompare(digest[:], entry.digest[:]) == 1 {
			return entry.orgID, true
		}
	}
	return "", false
}

// RequestID assigns a request-scoped identifier for tracing and log correlation.
func (m *MiddlewareStack) RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := newRequestID()
		ctx := context.WithValue(r.Context(), ctxKeyRequestID, requestID)
		r = r.WithContext(ctx)
		w.Header().Set("X-Request-Id", requestID)
		r.Header.Set("X-Request-Id", requestID)
		next.ServeHTTP(w, r)
	})
}

// StructuredLogging writes a single structured log entry for each request.
func (m *MiddlewareStack) StructuredLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rw := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}
		next.ServeHTTP(rw, r)
		m.logger.Info("request completed",
			"request_id", requestIDFrom(r.Context()),
			"org_id", orgIDFrom(r.Context()),
			"method", r.Method,
			"path", r.URL.Path,
			"status", rw.statusCode,
			"bytes", rw.bytes,
			"duration_ms", time.Since(start).Milliseconds(),
		)
		m.observeHTTP(r.URL.Path, rw.statusCode, time.Since(start))
	})
}

// metricRoute maps a request path to a bounded label value. Fixed paths are
// kept as-is; the approval decision endpoints carry a variable approval id that
// must not become one time series per id, so those paths collapse to a single
// normalized form per action.
func metricRoute(path string) string {
	const prefix = "/approvals/"
	if strings.HasPrefix(path, prefix) {
		rest := path[len(prefix):]
		if idx := strings.IndexByte(rest, '/'); idx >= 0 {
			suffix := strings.TrimRight(rest[idx:], "/")
			if suffix == "/approve" || suffix == "/reject" {
				return prefix + "{id}" + suffix
			}
		}
	}
	return path
}

// observeHTTP publishes one request to the shared registry: a counter keyed by
// route and status, and a duration histogram per route.
func (m *MiddlewareStack) observeHTTP(path string, status int, duration time.Duration) {
	if m.metrics == nil {
		return
	}
	route := metricRoute(path)
	m.metrics.Counter(httpRequestsMetric, "HTTP requests by route and status code", "route", "status").
		Add(1, route, strconv.Itoa(status))
	m.metrics.Histogram(httpDurationMetric, "HTTP request duration in seconds", nil, "route").
		Observe(duration.Seconds(), route)
}

// PanicRecovery converts panics into a structured 500 response and log entry.
func (m *MiddlewareStack) PanicRecovery(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				m.logger.Error("panic recovered",
					"request_id", requestIDFrom(r.Context()),
					"panic", rec,
				)
				writeError(w, http.StatusInternalServerError, "internal_error", "internal server error", requestIDFrom(r.Context()))
			}
		}()
		next.ServeHTTP(w, r)
	})
}

func newRequestID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	out := make([]byte, 36)
	hex.Encode(out[0:8], b[0:4])
	out[8] = '-'
	hex.Encode(out[9:13], b[4:6])
	out[13] = '-'
	hex.Encode(out[14:18], b[6:8])
	out[18] = '-'
	hex.Encode(out[19:23], b[8:10])
	out[23] = '-'
	hex.Encode(out[24:36], b[10:16])
	return string(out)
}

// responseWriter captures status and size while preserving the underlying writer.
type responseWriter struct {
	http.ResponseWriter
	statusCode int
	bytes      int
}

// WriteHeader records the response status before forwarding it downstream.
func (w *responseWriter) WriteHeader(statusCode int) {
	w.statusCode = statusCode
	w.ResponseWriter.WriteHeader(statusCode)
}

// Write tracks the written byte count while forwarding the payload downstream.
func (w *responseWriter) Write(b []byte) (int, error) {
	if w.statusCode == 0 {
		w.statusCode = http.StatusOK
	}
	n, err := w.ResponseWriter.Write(b)
	w.bytes += n
	return n, err
}

// Flush forwards the flush to the underlying writer when it supports it, so
// Server-Sent Events can stream through the middleware chain.
func (w *responseWriter) Flush() {
	if w.statusCode == 0 {
		w.statusCode = http.StatusOK
	}
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap exposes the underlying writer so http.ResponseController can reach
// connection-level controls, such as clearing the write deadline for SSE.
func (w *responseWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

func orgIDFrom(ctx context.Context) string {
	if v, ok := ctx.Value(ctxKeyOrgID).(string); ok {
		return v
	}
	return ""
}

func requestIDFrom(ctx context.Context) string {
	if v, ok := ctx.Value(ctxKeyRequestID).(string); ok {
		return v
	}
	return ""
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Warn("writeJSON failed", "error", err)
	}
}

func writeError(w http.ResponseWriter, status int, code, message, requestID string) {
	writeJSON(w, status, map[string]any{
		"request_id": requestID,
		"code":       code,
		"message":    message,
		"timestamp":  time.Now().UTC(),
	})
}
