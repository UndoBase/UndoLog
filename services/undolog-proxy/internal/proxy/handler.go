// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// It owns tool-call interception, canonical signature generation, upstream tool
// execution, and the commit/fail/approval routing that mirrors the Rust engine.
package proxy

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"time"

	"undolog-proxy/internal/approval"
	"undolog-proxy/internal/engine"
	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

// ErrToolExecutorNotConfigured is returned when no upstream tool executor exists.
var ErrToolExecutorNotConfigured = errors.New("tool executor not configured")

// ToolExecutor executes a proxied tool call against the upstream MCP tool server.
type ToolExecutor interface {
	Execute(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error)
}

// ToolExecutorFunc adapts a function into a ToolExecutor.
type ToolExecutorFunc func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error)

// Execute invokes the wrapped function.
func (f ToolExecutorFunc) Execute(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
	return f(ctx, call)
}

// HTTPToolExecutor forwards tool calls to an upstream HTTP endpoint.
type HTTPToolExecutor struct {
	baseURL string
	client  *http.Client
	metrics *metrics.Registry
}

// NewHTTPToolExecutor constructs an HTTP-based tool executor for the given
// endpoint. The client timeout mirrors the configured request timeout so a
// lower RequestTimeout setting shortens upstream waits instead of always
// waiting the hardcoded 30 seconds.
func NewHTTPToolExecutor(baseURL string, timeout time.Duration) (*HTTPToolExecutor, error) {
	if baseURL == "" {
		return nil, ErrToolExecutorNotConfigured
	}
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	return &HTTPToolExecutor{
		baseURL: baseURL,
		client:  &http.Client{Timeout: timeout},
	}, nil
}

// SetMetrics wires the registry so upstream execution latency and failures are
// exposed on /metrics. A nil registry disables instrumentation.
func (e *HTTPToolExecutor) SetMetrics(reg *metrics.Registry) {
	e.metrics = reg
}

// Execute posts the tool call to the upstream endpoint and decodes the result.
//
// A 4xx or 5xx upstream response is decoded as a ToolResult when the body
// looks like one, so a logical tool failure (for example the mock tool server's
// 404 with a {"success": false, "error": "customer not found"} body) flows
// through as a result instead of surfacing as a transport error and triggering
// Fail. Bodies that are not a ToolResult, including an empty body, still
// surface as a transport error.
func (e *HTTPToolExecutor) Execute(ctx context.Context, call protocol.ToolCall) (result protocol.ToolResult, err error) {
	start := time.Now()
	defer func() { e.observe(start, err) }()
	if e == nil {
		return protocol.ToolResult{}, ErrToolExecutorNotConfigured
	}
	body, err := json.Marshal(call)
	if err != nil {
		return protocol.ToolResult{}, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.baseURL, bytes.NewReader(body))
	if err != nil {
		return protocol.ToolResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := e.client.Do(req)
	if err != nil {
		return protocol.ToolResult{}, err
	}
	defer resp.Body.Close()

	payload, err := io.ReadAll(resp.Body)
	if err != nil {
		return protocol.ToolResult{}, err
	}
	if resp.StatusCode >= 400 {
		if isToolResultBody(payload) {
			var result protocol.ToolResult
			if err := json.Unmarshal(payload, &result); err == nil {
				return result, nil
			}
		}
		if len(payload) == 0 {
			return protocol.ToolResult{}, errors.New(resp.Status)
		}
		return protocol.ToolResult{}, errors.New(string(payload))
	}

	if len(payload) > 0 {
		if err := json.Unmarshal(payload, &result); err != nil {
			return protocol.ToolResult{}, err
		}
	}
	return result, nil
}

// Metric names exposed by the tool executor.
const (
	executorDurationMetric = "undolog_proxy_executor_duration_seconds"
)

// observe publishes upstream execution duration to the shared registry. The
// result label distinguishes outcome from transport error.
func (e *HTTPToolExecutor) observe(start time.Time, err error) {
	if e == nil || e.metrics == nil {
		return
	}
	result := "success"
	if err != nil {
		result = "error"
	}
	e.metrics.Histogram(executorDurationMetric, "Upstream tool executor duration in seconds", nil, "result").
		Observe(time.Since(start).Seconds(), result)
}

// isToolResultBody reports whether a non-2xx upstream payload looks like a
// serialized protocol.ToolResult rather than an arbitrary error document. The
// ToolResult JSON contract always carries at least one of the success, output,
// or error keys, so their presence distinguishes a structured tool result from
// a proxy error page or a plain-text status line.
func isToolResultBody(payload []byte) bool {
	if len(payload) == 0 {
		return false
	}
	var keys map[string]json.RawMessage
	if err := json.Unmarshal(payload, &keys); err != nil {
		return false
	}
	for _, key := range []string{"success", "output", "error"} {
		if _, ok := keys[key]; ok {
			return true
		}
	}
	return false
}

// Handler intercepts tool calls, routes them through the engine, and emits SSE events.
type Handler struct {
	engineClient   protocol.EngineClient
	executor       ToolExecutor
	approvals      *approval.Store
	broadcaster    *sse.Broadcaster
	requestTimeout time.Duration
	maxBodyBytes   int64
	logger         *slog.Logger
}

// NewHandler wires the engine client, executor, approval store, and broadcaster together.
func NewHandler(engineClient protocol.EngineClient, executor ToolExecutor, approvals *approval.Store, broadcaster *sse.Broadcaster, requestTimeout time.Duration, maxBodyBytes int64, logger *slog.Logger) *Handler {
	if logger == nil {
		logger = slog.Default()
	}
	if maxBodyBytes <= 0 {
		maxBodyBytes = 1 << 20
	}
	return &Handler{
		engineClient:   engineClient,
		executor:       executor,
		approvals:      approvals,
		broadcaster:    broadcaster,
		requestTimeout: requestTimeout,
		maxBodyBytes:   maxBodyBytes,
		logger:         logger,
	}
}

type toolCallRequest struct {
	SessionID   string          `json:"session_id"`
	ToolName    string          `json:"tool_name"`
	ToolVersion string          `json:"tool_version,omitempty"`
	StepIndex   uint32          `json:"step_index,omitempty"`
	Args        json.RawMessage `json:"args"`
}

// ServeHTTP processes POST /mcp/tool_call requests through the interception flow.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "POST required", requestIDFrom(r.Context()))
		return
	}

	orgID := orgIDFrom(r.Context())
	if orgID == "" {
		writeError(w, http.StatusUnauthorized, "auth_failed", "org id missing from request context", requestIDFrom(r.Context()))
		return
	}

	var req toolCallRequest
	err := json.NewDecoder(http.MaxBytesReader(w, r.Body, h.maxBodyBytes)).Decode(&req)
	if err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			writeError(w, http.StatusRequestEntityTooLarge, "body_too_large", "request body exceeds the configured limit", requestIDFrom(r.Context()))
		} else {
			writeError(w, http.StatusBadRequest, "invalid_request", "invalid JSON body", requestIDFrom(r.Context()))
		}
		return
	}
	if req.SessionID == "" || req.ToolName == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "session_id and tool_name are required", requestIDFrom(r.Context()))
		return
	}

	signature, err := CanonicalSignature(req.ToolName, req.Args)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_request", "unable to canonicalize tool args", requestIDFrom(r.Context()))
		return
	}

	call := protocol.ToolCall{
		OrgID:       orgID,
		SessionID:   req.SessionID,
		ToolName:    req.ToolName,
		ToolVersion: req.ToolVersion,
		StepIndex:   req.StepIndex,
		Args:        req.Args,
		Signature:   signature,
	}

	ctx := engine.WithRequestID(r.Context(), requestIDFrom(r.Context()))
	if h.requestTimeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, h.requestTimeout)
		defer cancel()
	}

	outcome, err := h.engineClient.Intercept(ctx, protocol.InterceptRequest{ToolCall: call})
	if err != nil {
		h.logger.Error("intercept failed", "request_id", requestIDFrom(r.Context()), "error", err)
		h.emit(sse.Event{
			Type:      sse.EventEffectFailed,
			OrgID:     orgID,
			SessionID: req.SessionID,
			Payload:   json.RawMessage(`{"stage":"intercept","error":"failed"}`),
		})
		writeError(w, http.StatusBadGateway, "intercept_failed", err.Error(), requestIDFrom(r.Context()))
		return
	}

	h.emit(sse.Event{
		Type:      sse.EventEffectIntercepted,
		OrgID:     orgID,
		SessionID: req.SessionID,
		Payload:   json.RawMessage(`{"stage":"intercepted"}`),
	})

	// Route the engine decision into the appropriate execution path.
	switch outcome.Outcome {
	case protocol.InterceptExecute:
		result, execErr := h.executor.Execute(ctx, call)
		if execErr != nil {
			if failErr := h.engineClient.Fail(ctx, protocol.FailRequest{
				OrgID:     orgID,
				SessionID: req.SessionID,
				EffectID:  outcome.EffectID,
				Error:     execErr.Error(),
			}); failErr != nil {
				h.logger.Error("commit failed after execution failure", "effect_id", outcome.EffectID, "exec_error", execErr, "fail_error", failErr)
			}
			h.emit(sse.Event{
				Type:      sse.EventEffectFailed,
				OrgID:     orgID,
				SessionID: req.SessionID,
				EffectID:  outcome.EffectID,
				Payload:   json.RawMessage(`{"stage":"execute","error":"failed"}`),
			})
			writeError(w, http.StatusBadGateway, "tool_error", execErr.Error(), requestIDFrom(r.Context()))
			return
		}
		if err := h.engineClient.Commit(ctx, protocol.CommitRequest{
			OrgID:     orgID,
			SessionID: req.SessionID,
			EffectID:  outcome.EffectID,
			Result:    result,
		}); err != nil {
			h.emit(sse.Event{
				Type:      sse.EventEffectFailed,
				OrgID:     orgID,
				SessionID: req.SessionID,
				EffectID:  outcome.EffectID,
				Payload:   json.RawMessage(`{"stage":"commit","error":"failed"}`),
			})
			writeError(w, http.StatusBadGateway, "commit_failed", err.Error(), requestIDFrom(r.Context()))
			return
		}
		h.emit(sse.Event{
			Type:      sse.EventEffectCommitted,
			OrgID:     orgID,
			SessionID: req.SessionID,
			EffectID:  outcome.EffectID,
			Payload:   json.RawMessage(`{"stage":"committed"}`),
		})
		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "executed",
			"effect_id": outcome.EffectID,
			"result":    result,
		})
	case protocol.InterceptReplay:
		h.emit(sse.Event{
			Type:      sse.EventEffectReplayed,
			OrgID:     orgID,
			SessionID: req.SessionID,
			EffectID:  outcome.EffectID,
			Payload:   json.RawMessage(`{"stage":"replayed"}`),
		})
		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "replayed",
			"effect_id": outcome.EffectID,
			"result":    outcome.CachedResult,
		})
	case protocol.InterceptAwaitingApproval:
		approvalID := outcome.ApprovalID
		if h.approvals != nil {
			rec := h.approvals.Create(approval.Record{
				ID:        approvalID,
				OrgID:     orgID,
				SessionID: req.SessionID,
				EffectID:  outcome.EffectID,
				ToolName:  req.ToolName,
				Args:      append([]byte(nil), req.Args...),
				Status:    approval.StatusPending,
			})
			approvalID = rec.ID
		}
		h.emit(sse.Event{
			Type:       sse.EventApprovalRequired,
			OrgID:      orgID,
			SessionID:  req.SessionID,
			EffectID:   outcome.EffectID,
			ApprovalID: approvalID,
			Payload:    json.RawMessage(`{"stage":"approval_required"}`),
		})
		writeJSON(w, http.StatusAccepted, map[string]any{
			"status":      "pending_approval",
			"approval_id": approvalID,
			"retry_after": 5,
		})
	default:
		writeError(w, http.StatusInternalServerError, "intercept_failed", "unexpected intercept outcome", requestIDFrom(r.Context()))
	}
}

func (h *Handler) emit(evt sse.Event) {
	if h.broadcaster != nil {
		h.broadcaster.Emit(evt)
	}
}
