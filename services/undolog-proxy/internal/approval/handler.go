// Package approval stores and resolves approval requests for irreversible calls.
//
// It exposes the approval list and decision endpoints used when the engine
// pauses an irreversible effect until a human approves or rejects it.
package approval

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"undolog-proxy/internal/engine"
	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

// Metric names published by the approval handler.
const (
	approvalDecisionsMetric = "undolog_proxy_approval_decisions_total"
	approvalDurationMetric  = "undolog_proxy_approval_decision_duration_seconds"
)

type eventBroadcaster interface {
	Emit(evt sse.Event)
}

// ExecuteApprovedFn executes a tool call that has been approved.
// The proxy wires this callback at construction time to preserve layering.
type ExecuteApprovedFn func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error)

// Handler exposes approval listing and decision endpoints.
type Handler struct {
	store          *Store
	engine         protocol.EngineClient
	executeFn      ExecuteApprovedFn
	broadcaster    eventBroadcaster
	logger         *slog.Logger
	requestTimeout time.Duration
	metrics        *metrics.Registry
}

// NewHandler wires the approval store, engine client, executor callback, and broadcaster together.
func NewHandler(store *Store, engineClient protocol.EngineClient, executeFn ExecuteApprovedFn, broadcaster eventBroadcaster, requestTimeout time.Duration, logger *slog.Logger) *Handler {
	if logger == nil {
		logger = slog.Default()
	}
	if store == nil {
		store = NewStore()
	}
	return &Handler{
		store:          store,
		engine:         engineClient,
		executeFn:      executeFn,
		broadcaster:    broadcaster,
		requestTimeout: requestTimeout,
		logger:         logger,
	}
}

// SetMetrics wires the registry so decision latency and outcome counts are
// exposed on /metrics. A nil registry disables instrumentation.
func (h *Handler) SetMetrics(reg *metrics.Registry) {
	h.metrics = reg
}

// ListApprovals returns approval requests filtered by organization and status,
// ordered newest first and bounded by the limit query parameter.
func (h *Handler) ListApprovals(w http.ResponseWriter, r *http.Request) {
	orgID := strings.TrimSpace(r.Header.Get("X-Org-Id"))
	if orgID == "" {
		http.Error(w, "org_id required", http.StatusUnauthorized)
		return
	}

	status := Status(strings.ToLower(strings.TrimSpace(r.URL.Query().Get("state"))))
	switch status {
	case "", "pending":
		status = StatusPending
	case "approved":
		status = StatusApproved
	case "rejected":
		status = StatusRejected
	default:
		http.Error(w, "invalid state", http.StatusBadRequest)
		return
	}

	writeJSON(w, h.store.List(orgID, status, parseLimit(r.URL.Query().Get("limit"))))
}

// ApproveApproval marks one pending approval as approved and resumes the engine.
func (h *Handler) ApproveApproval(w http.ResponseWriter, r *http.Request) {
	h.resolveDecision(w, r, StatusApproved)
}

// RejectApproval marks one pending approval as rejected and resumes the engine.
func (h *Handler) RejectApproval(w http.ResponseWriter, r *http.Request) {
	h.resolveDecision(w, r, StatusRejected)
}

// Health reports a minimal readiness response for the approval subsystem.
func (h *Handler) Health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, map[string]any{"status": "ok"})
}

// CreatePending stores a new pending approval record for one intercepted call.
func (h *Handler) CreatePending(orgID, sessionID, effectID, toolName string, args []byte) Record {
	return h.store.Create(Record{
		OrgID:     orgID,
		SessionID: sessionID,
		EffectID:  effectID,
		ToolName:  toolName,
		Args:      args,
		Status:    StatusPending,
	})
}

func (h *Handler) resolveDecision(w http.ResponseWriter, r *http.Request, target Status) {
	action := "approve"
	if target == StatusRejected {
		action = "reject"
	}

	orgID := strings.TrimSpace(r.Header.Get("X-Org-Id"))
	if orgID == "" {
		http.Error(w, "org_id required", http.StatusUnauthorized)
		return
	}

	id, ok := approvalIDFromPath(r.URL.Path)
	if !ok {
		http.Error(w, "approval id required", http.StatusBadRequest)
		return
	}

	if rec, exists := h.store.Get(id); !exists || rec.OrgID != orgID {
		http.Error(w, "approval not found", http.StatusNotFound)
		return
	}

	// Validate the decision body before mutating state so a malformed request
	// never briefly flips the record to a terminal status.
	body, ok := h.decodeDecisionBody(w, r)
	if !ok {
		return
	}
	if body.Actor == "" {
		body.Actor = "unknown"
	}

	// Resolve the decision atomically so a concurrent double-approve returns
	// 409 from the proxy itself instead of a 502 from the engine's optimistic
	// lock.
	start := time.Now()
	defer func() {
		if h.metrics != nil {
			h.metrics.Histogram(approvalDurationMetric, "Approval decision duration in seconds", nil, "action").
				Observe(time.Since(start).Seconds(), action)
		}
	}()
	rec, ok := h.store.CompareAndSwap(id, StatusPending, target)
	if !ok {
		h.recordDecision(action, "conflict")
		http.Error(w, "approval already resolved", http.StatusConflict)
		return
	}

	ctx := engine.WithRequestID(r.Context(), strings.TrimSpace(r.Header.Get("X-Request-Id")))
	if h.requestTimeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, h.requestTimeout)
		defer cancel()
	}
	switch target {
	case StatusApproved:
		approveResp, err := h.engine.Approve(ctx, protocol.ApproveRequest{
			OrgID:        orgID,
			ApprovalID:   id,
			Actor:        body.Actor,
			ApprovedArgs: body.ApprovedArgs,
		})
		if err != nil {
			h.store.RestorePending(id)
			h.recordDecision(action, "engine_error")
			h.failDecision(w, id, err)
			return
		}

		// Execute the approved tool.
		call := protocol.ToolCall{
			OrgID:       orgID,
			SessionID:   approveResp.SessionID,
			ToolName:    approveResp.ToolName,
			ToolVersion: approveResp.ToolVersion,
			Args:        approveResp.Args,
		}
		result, execErr := h.executeFn(ctx, call)
		if execErr != nil {
			h.logger.Error("execution failed after approval", "effect_id", approveResp.EffectID, "error", execErr)
			// Report the failure through Fail so the effect is not falsely
			// recorded as committed in the engine audit trail.
			if failErr := h.engine.Fail(ctx, protocol.FailRequest{
				OrgID:     orgID,
				SessionID: approveResp.SessionID,
				EffectID:  approveResp.EffectID,
				Error:     execErr.Error(),
			}); failErr != nil {
				h.logger.Error("fail failed after approved execution failure", "effect_id", approveResp.EffectID, "exec_error", execErr, "fail_error", failErr)
			}
		} else if commitErr := h.engine.Commit(ctx, protocol.CommitRequest{
			OrgID:     orgID,
			SessionID: approveResp.SessionID,
			EffectID:  approveResp.EffectID,
			Result:    result,
		}); commitErr != nil {
			h.logger.Error("commit failed after approved execution", "effect_id", approveResp.EffectID, "commit_error", commitErr)
		}

		if h.broadcaster != nil {
			payload, mErr := json.Marshal(rec)
			if mErr != nil {
				h.logger.Error("marshal approved record for SSE", "approval_id", id, "error", mErr)
			} else {
				h.broadcaster.Emit(sse.Event{
					Type:       sse.EventApprovalApproved,
					OrgID:      rec.OrgID,
					SessionID:  rec.SessionID,
					EffectID:   approveResp.EffectID,
					ApprovalID: rec.ID,
					Payload:    payload,
				})
			}
		}

		resp := map[string]any{
			"status":      string(target),
			"approval_id": id,
			"effect_id":   approveResp.EffectID,
		}
		if execErr != nil {
			resp["execution"] = "failed"
			resp["error"] = execErr.Error()
		} else {
			resp["execution"] = "committed"
			resp["result"] = result
		}
		writeJSON(w, resp)
		h.recordDecision(action, "applied")

	case StatusRejected:
		if rejErr := h.engine.Reject(ctx, protocol.RejectRequest{OrgID: orgID, ApprovalID: id, Actor: body.Actor}); rejErr != nil {
			h.store.RestorePending(id)
			h.recordDecision(action, "engine_error")
			h.failDecision(w, id, rejErr)
			return
		}

		if h.broadcaster != nil {
			payload, mErr := json.Marshal(rec)
			if mErr != nil {
				h.logger.Error("marshal rejected record for SSE", "approval_id", id, "error", mErr)
			} else {
				h.broadcaster.Emit(sse.Event{
					Type:       sse.EventApprovalRejected,
					OrgID:      rec.OrgID,
					SessionID:  rec.SessionID,
					EffectID:   rec.EffectID,
					ApprovalID: rec.ID,
					Payload:    payload,
				})
			}
		}

		writeJSON(w, map[string]any{
			"status":      string(target),
			"approval_id": id,
		})
		h.recordDecision(action, "applied")

	default:
		h.store.RestorePending(id)
		http.Error(w, "unsupported action", http.StatusBadRequest)
	}
}

// decisionBody is the optional JSON payload accepted by the approve and reject
// endpoints.
type decisionBody struct {
	Actor        string          `json:"actor"`
	ApprovedArgs json.RawMessage `json:"approved_args"`
}

// decodeDecisionBody reads the optional decision body. An empty body is allowed;
// a malformed one yields a 400 so typos surface instead of silently becoming an
// empty actor.
func (h *Handler) decodeDecisionBody(w http.ResponseWriter, r *http.Request) (decisionBody, bool) {
	var body decisionBody
	if r.Body == nil || r.Body == http.NoBody {
		return body, true
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		if errors.Is(err, io.EOF) {
			return body, true
		}
		h.logger.Warn("malformed decision body", "error", err)
		http.Error(w, "malformed request body", http.StatusBadRequest)
		return decisionBody{}, false
	}
	return body, true
}

// DefaultListLimit bounds GET /approvals when no limit is supplied.
const DefaultListLimit = 100

// maxListLimit caps the GET /approvals limit query parameter.
const maxListLimit = 500

// parseLimit parses the limit query parameter, falling back to the default and
// capping the result so callers cannot request unbounded listings.
func parseLimit(raw string) int {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return DefaultListLimit
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return DefaultListLimit
	}
	if n > maxListLimit {
		return maxListLimit
	}
	return n
}

func (h *Handler) failDecision(w http.ResponseWriter, id string, err error) {
	h.logger.Error("approval decision failed", "approval_id", id, "error", err)
	http.Error(w, "engine rejected the decision", http.StatusBadGateway)
}

// recordDecision increments the decision outcome counter.
func (h *Handler) recordDecision(action, result string) {
	if h.metrics == nil {
		return
	}
	h.metrics.Counter(approvalDecisionsMetric, "Approval decisions by action and outcome", "action", "result").
		Add(1, action, result)
}

func approvalIDFromPath(path string) (string, bool) {
	path = strings.TrimSuffix(path, "/")
	if !strings.HasPrefix(path, "/approvals/") {
		return "", false
	}
	parts := strings.Split(strings.TrimPrefix(path, "/approvals/"), "/")
	if len(parts) != 2 {
		return "", false
	}
	if parts[1] != "approve" && parts[1] != "reject" {
		return "", false
	}
	return parts[0], true
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Warn("writeJSON failed", "error", err)
	}
}
