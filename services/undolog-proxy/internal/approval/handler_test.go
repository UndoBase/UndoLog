// Package approval tests approval listing and decision handling.
//
// These tests exercise the in-memory approval state machine and the engine
// callbacks that resume or reject suspended effects.
package approval

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

type mockEngine struct {
	approved []string
	rejected []string
}

// Intercept is a no-op stub used to satisfy the engine interface.
func (m *mockEngine) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	return protocol.InterceptResponse{}, nil
}

// Commit is a no-op stub used to satisfy the engine interface.
func (m *mockEngine) Commit(ctx context.Context, req protocol.CommitRequest) error { return nil }

// Fail is a no-op stub used to satisfy the engine interface.
func (m *mockEngine) Fail(ctx context.Context, req protocol.FailRequest) error { return nil }

// Approve records that the approval was resumed and returns a valid response.
func (m *mockEngine) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	m.approved = append(m.approved, req.ApprovalID)
	return protocol.ApproveResponse{
		EffectID:  "eff-1",
		SessionID: "sess-1",
		ToolName:  "delete_user",
		Args:      []byte(`{"id":"u1"}`),
	}, nil
}

// Reject records that the approval was denied by the handler.
func (m *mockEngine) Reject(ctx context.Context, req protocol.RejectRequest) error {
	m.rejected = append(m.rejected, req.ApprovalID)
	return nil
}

// Close is a no-op stub used to satisfy the engine interface.
func (m *mockEngine) Close() error { return nil }

// TestApprovalLifecycle verifies listing and approval resolution in sequence.
func TestApprovalLifecycle(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	broadcaster := sse.NewBroadcaster(4)
	mockExec := func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{Success: true, Output: []byte(`{"deleted":true}`)}, nil
	}
	handler := NewHandler(store, engine, mockExec, broadcaster, 0, nil)

	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{"id":"u1"}`))

	req := httptest.NewRequest(http.MethodGet, "/approvals?state=pending", nil)
	req.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.ListApprovals(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", w.Code)
	}
	var listed []Record
	if err := json.Unmarshal(w.Body.Bytes(), &listed); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	if len(listed) != 1 || listed[0].ID != rec.ID {
		t.Fatalf("unexpected list: %+v", listed)
	}

	approveReq := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/approve", nil)
	approveReq.Header.Set("X-Org-Id", "org-1")
	approveRec := httptest.NewRecorder()
	handler.ApproveApproval(approveRec, approveReq)
	if approveRec.Code != http.StatusOK {
		t.Fatalf("approve status: %d", approveRec.Code)
	}
	if len(engine.approved) != 1 || engine.approved[0] != rec.ID {
		t.Fatalf("approve not forwarded: %+v", engine.approved)
	}
}
