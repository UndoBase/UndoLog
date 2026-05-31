// Package approval stores and resolves approval requests for irreversible calls.
//
// It exposes the approval list and decision endpoints used when the engine
// pauses an irreversible effect until a human approves or rejects it.
package approval

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"

	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

type eventBroadcaster interface {
	Emit(evt sse.Event)
}

// ExecuteApprovedFn executes a tool call that has been approved.
// The proxy wires this callback at construction time to preserve layering.
type ExecuteApprovedFn func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error)

// Handler exposes approval listing and decision endpoints.
type Handler struct {
	store       *Store
	engine      protocol.EngineClient
	executeFn   ExecuteApprovedFn
	broadcaster eventBroadcaster
	logger      *slog.Logger
}

// NewHandler wires the approval store, engine client, executor callback, and broadcaster together.
func NewHandler(store *Store, engine protocol.EngineClient, executeFn ExecuteApprovedFn, broadcaster eventBroadcaster, logger *slog.Logger) *Handler {
	if logger == nil {
		logger = slog.Default()
	}
	if store == nil {
		store = NewStore()
	}
	return &Handler{
		store:       store,
		engine:      engine,
		executeFn:   executeFn,
		broadcaster: broadcaster,
		logger:      logger,
	}
}

// ListApprovals returns approval requests filtered by organization and status.
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

	writeJSON(w, h.store.List(orgID, status))
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

	rec, exists := h.store.Get(id)
	if !exists || rec.OrgID != orgID {
		http.Error(w, "approval not found", http.StatusNotFound)
		return
	}
	// Only pending requests can transition to a terminal decision.
	if rec.Status != StatusPending {
		http.Error(w, "approval already resolved", http.StatusConflict)
		return
	}

	ctx := r.Context()
	switch target {
	case StatusApproved:
		// Parse optional actor and approved_args from the request body.
		var body struct {
			Actor        string          `json:"actor"`
			ApprovedArgs json.RawMessage `json:"approved_args"`
		}
		if r.Body != nil {
			_ = json.NewDecoder(r.Body).Decode(&body)
		}
		if body.Actor == "" {
			body.Actor = "unknown"
		}

		approveResp, err := h.engine.Approve(ctx, protocol.ApproveRequest{
			OrgID:        orgID,
			ApprovalID:   id,
			Actor:        body.Actor,
			ApprovedArgs: body.ApprovedArgs,
		})
		if err != nil {
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
			// Approval already recorded; commit the failure so the engine records it.
			if commitErr := h.engine.Commit(ctx, protocol.CommitRequest{
				OrgID:     orgID,
				SessionID: approveResp.SessionID,
				EffectID:  approveResp.EffectID,
				Result:    protocol.ToolResult{Success: false, Error: execErr.Error()},
			}); commitErr != nil {
				h.logger.Error("commit failed after approved execution failure", "effect_id", approveResp.EffectID, "exec_error", execErr, "commit_error", commitErr)
			}
		} else if commitErr := h.engine.Commit(ctx, protocol.CommitRequest{
			OrgID:     orgID,
			SessionID: approveResp.SessionID,
			EffectID:  approveResp.EffectID,
			Result:    result,
		}); commitErr != nil {
			h.logger.Error("commit failed after approved execution", "effect_id", approveResp.EffectID, "commit_error", commitErr)
		}

		rec, _ = h.store.UpdateStatus(id, target)
		if h.broadcaster != nil {
			payload, _ := json.Marshal(rec)
			h.broadcaster.Emit(sse.Event{
				Type:       sse.EventApprovalApproved,
				OrgID:      rec.OrgID,
				SessionID:  rec.SessionID,
				EffectID:   approveResp.EffectID,
				ApprovalID: rec.ID,
				Payload:    payload,
			})
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

	case StatusRejected:
		if err := h.engine.Reject(ctx, protocol.RejectRequest{OrgID: orgID, ApprovalID: id}); err != nil {
			h.failDecision(w, id, err)
			return
		}

		rec, _ = h.store.UpdateStatus(id, target)
		if h.broadcaster != nil {
			payload, _ := json.Marshal(rec)
			h.broadcaster.Emit(sse.Event{
				Type:       sse.EventApprovalRejected,
				OrgID:      rec.OrgID,
				SessionID:  rec.SessionID,
				EffectID:   rec.EffectID,
				ApprovalID: rec.ID,
				Payload:    payload,
			})
		}

		writeJSON(w, map[string]any{
			"status":      string(target),
			"approval_id": id,
		})

	default:
		http.Error(w, "unsupported action", http.StatusBadRequest)
	}
}

func (h *Handler) failDecision(w http.ResponseWriter, id string, err error) {
	h.logger.Error("approval decision failed", "approval_id", id, "error", err)
	http.Error(w, "engine rejected the decision", http.StatusBadGateway)
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
