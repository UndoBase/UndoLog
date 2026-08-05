// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// The server composes the proxy handler, approval workflow, SSE broadcaster,
// and middleware chain into one HTTP surface with graceful shutdown support.
package proxy

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"undolog-proxy/internal/approval"
	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

// Server composes the proxy, approval, and SSE subsystems into one HTTP service.
type Server struct {
	cfg               Config
	engine            protocol.EngineClient
	tool              ToolExecutor
	approvals         *approval.Store
	broadcaster       *sse.Broadcaster
	handler           http.Handler
	httpSrv           http.Server
	reconcilerOrgs    []string
	reconcileInterval time.Duration
	approvalRetention time.Duration
	logger            *slog.Logger
}

// NewServer builds the full HTTP server stack for the proxy service.
func NewServer(cfg Config, engine protocol.EngineClient, tool ToolExecutor, logger *slog.Logger) (*Server, error) {
	if logger == nil {
		logger = slog.Default()
	}
	if engine == nil {
		return nil, errors.New("engine client is required")
	}
	if tool == nil {
		return nil, errors.New("tool executor is required")
	}

	broadcaster := sse.NewBroadcaster(cfg.DashboardEventBufSize)
	approvalStore := approval.NewStore()
	executeApproved := func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return tool.Execute(ctx, call)
	}
	approvalHandler := approval.NewHandler(approvalStore, engine, executeApproved, broadcaster, cfg.RequestTimeout, logger)
	mw := NewMiddlewareStack(logger, cfg.TrustedAPIKeys)
	handler := NewHandler(engine, tool, approvalStore, broadcaster, cfg.RequestTimeout, logger)

	protected := http.NewServeMux()
	protected.Handle("/mcp/tool_call", handler)
	protected.Handle("/events", broadcaster)
	protected.HandleFunc("/approvals", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			approvalHandler.ListApprovals(w, r)
			return
		}
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	})
	protected.HandleFunc("/approvals/", func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/approve") && r.Method == http.MethodPost:
			approvalHandler.ApproveApproval(w, r)
		case strings.HasSuffix(r.URL.Path, "/reject") && r.Method == http.MethodPost:
			approvalHandler.RejectApproval(w, r)
		case strings.HasSuffix(r.URL.Path, "/approve"), strings.HasSuffix(r.URL.Path, "/reject"):
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		default:
			http.NotFound(w, r)
		}
	})

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":       "ok",
			"service":      "undolog-proxy",
			"engine_addr":  cfg.EngineGRPCAddr,
			"upstream_url": cfg.UpstreamToolURL,
		})
	})
	protectedChain := mw.PanicRecovery(mw.RequestID(mw.StructuredLogging(mw.Auth(protected))))
	mux.Handle("/mcp/tool_call", protectedChain)
	mux.Handle("/events", protectedChain)
	mux.Handle("/approvals", protectedChain)
	mux.Handle("/approvals/", protectedChain)

	return &Server{
		cfg:               cfg,
		engine:            engine,
		tool:              tool,
		approvals:         approvalStore,
		broadcaster:       broadcaster,
		handler:           mux,
		reconcilerOrgs:    orgsFromKeys(cfg.TrustedAPIKeys),
		reconcileInterval: cfg.ApprovalReconcileInterval,
		approvalRetention: cfg.ApprovalRetention,
		httpSrv: http.Server{
			Addr:         cfg.ListenAddr,
			Handler:      mux,
			ReadTimeout:  cfg.ReadTimeout,
			WriteTimeout: cfg.WriteTimeout,
		},
		logger: logger,
	}, nil
}

// orgsFromKeys returns the unique organization identifiers served by a set of
// trusted API keys, in stable order.
func orgsFromKeys(keys map[string]string) []string {
	seen := make(map[string]struct{}, len(keys))
	out := make([]string, 0, len(keys))
	for _, org := range keys {
		if org == "" {
			continue
		}
		if _, ok := seen[org]; ok {
			continue
		}
		seen[org] = struct{}{}
		out = append(out, org)
	}
	return out
}

// Start runs the HTTP server until the context is canceled or the listener exits.
func (s *Server) Start(ctx context.Context) error {
	// Populate the approval view from the engine before accepting requests so a
	// proxy restart does not orphan pending approvals.
	s.reconcileOnce(ctx)

	errCh := make(chan error, 1)
	go func() {
		s.logger.Info("proxy server starting", "addr", s.cfg.ListenAddr)
		errCh <- s.httpSrv.ListenAndServe()
	}()

	// Refresh the approval view and sweep expired records on an interval.
	go approval.RunApprovalReconciler(ctx, s.engine, s.approvals, s.reconcilerOrgs, s.reconcileInterval, s.approvalRetention, s.logger)

	select {
	case <-ctx.Done():
		// Close subscriber channels first so long-lived /events streams end and
		// graceful shutdown is not blocked waiting on them.
		s.broadcaster.Close()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), s.cfg.ShutdownTimeout)
		defer cancel()
		if err := s.httpSrv.Shutdown(shutdownCtx); err != nil {
			s.logger.Warn("shutdown error", "error", err)
		}
		return nil
	case err := <-errCh:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}

// reconcileOnce synchronously rebuilds the approval view from the engine for
// every served organization, bounded by the configured request timeout. The
// reconcile result is not needed here because the startup pass does not sweep.
func (s *Server) reconcileOnce(parent context.Context) {
	ctx := parent
	if s.cfg.RequestTimeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(parent, s.cfg.RequestTimeout)
		defer cancel()
	}
	approval.ReconcileApprovals(ctx, s.engine, s.approvals, s.reconcilerOrgs, s.logger)
}
