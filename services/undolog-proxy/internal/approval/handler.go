// Package approval stores and resolves approval requests for irreversible calls.
//
// It exposes the approval list and decision endpoints used when the engine
// pauses an irreversible effect until a human approves or rejects it.
package approval

import (
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

// Handler exposes approval listing and decision endpoints.
type Handler struct {
	store       *Store
	engine      protocol.EngineClient
	broadcaster eventBroadcaster
	logger      *slog.Logger
}

// NewHandler wires the approval store, engine client, and broadcaster together.
func NewHandler(store *Store, engine protocol.EngineClient, broadcaster eventBroadcaster, logger *slog.Logger) *Handler {
	if logger == nil {
		logger = slog.Default()
	}
	if store == nil {
		store = NewStore()
	}
	return &Handler{
		store:       store,
		engine:      engine,
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

	writeJSON(w, http.StatusOK, h.store.List(orgID, status))
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
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok"})
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
	// Tell the engine to resume or stop the suspended effect before mutating local state.
	switch target {
	case StatusApproved:
		if err := h.engine.Approve(ctx, protocol.ApproveRequest{ApprovalID: id}); err != nil {
			h.failDecision(w, id, err)
			return
		}
	case StatusRejected:
		if err := h.engine.Reject(ctx, protocol.RejectRequest{ApprovalID: id}); err != nil {
			h.failDecision(w, id, err)
			return
		}
	default:
		http.Error(w, "unsupported action", http.StatusBadRequest)
		return
	}

	rec, _ = h.store.UpdateStatus(id, target)
	if h.broadcaster != nil {
		payload, _ := json.Marshal(rec)
		eventType := sse.EventApprovalApproved
		if target == StatusRejected {
			eventType = sse.EventApprovalRejected
		}
		h.broadcaster.Emit(sse.Event{
			Type:       eventType,
			OrgID:      rec.OrgID,
			SessionID:  rec.SessionID,
			EffectID:   rec.EffectID,
			ApprovalID: rec.ID,
			Payload:    payload,
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":      string(target),
		"approval_id": id,
	})
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

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Warn("writeJSON failed", "error", err)
	}
}
