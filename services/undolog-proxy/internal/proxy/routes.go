// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
package proxy

import (
	"log/slog"
	"net/http"
)

// MountAdminRoutes registers administrative endpoints under /api/v1/admin/
// behind authentication and admin-only role verification.
func MountAdminRoutes(mux *http.ServeMux, mw *MiddlewareStack, admin DeadLetterAdmin, logger *slog.Logger) {
	adminHandler := NewAdminHandler(admin, logger)

	adminMux := http.NewServeMux()
	adminMux.HandleFunc("/api/v1/admin/dead-letters", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			reqID := requestIDFrom(r.Context())
			writeError(w, logger, http.StatusMethodNotAllowed, "method_not_allowed", "method not allowed", reqID)
			return
		}
		adminHandler.ListDeadLetters(w, r)
	})

	protectedAdmin := mw.PanicRecovery(mw.RequestID(mw.StructuredLogging(mw.Auth(mw.AdminOnly(adminMux)))))
	mux.Handle("/api/v1/admin/dead-letters", protectedAdmin)
}
