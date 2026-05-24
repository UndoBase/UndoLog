// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// The middleware chain resolves organization identity from API keys, assigns
// request IDs, writes structured logs, and converts panics into JSON errors.
package proxy

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

type ctxKey string

const (
	ctxKeyOrgID     ctxKey = "org_id"
	ctxKeyRequestID ctxKey = "request_id"
)

// MiddlewareStack composes the proxy middlewares into one reusable bundle.
type MiddlewareStack struct {
	logger         *slog.Logger
	trustedAPIKeys map[string]string
}

// NewMiddlewareStack builds the middleware chain used by the proxy server.
func NewMiddlewareStack(logger *slog.Logger, trustedAPIKeys map[string]string) *MiddlewareStack {
	if logger == nil {
		logger = slog.Default()
	}
	return &MiddlewareStack{logger: logger, trustedAPIKeys: trustedAPIKeys}
}

// Auth resolves X-Api-Key to an organization ID and stores it on the request.
func (m *MiddlewareStack) Auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		apiKey := strings.TrimSpace(r.Header.Get("X-Api-Key"))
		if apiKey == "" {
			writeError(w, http.StatusUnauthorized, "auth_failed", "X-Api-Key header required", "")
			return
		}
		orgID, ok := m.trustedAPIKeys[apiKey]
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
	})
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
