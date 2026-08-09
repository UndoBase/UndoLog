// Package approval tests approval listing and decision handling.
//
// These tests exercise the in-memory approval state machine and the engine
// callbacks that resume or reject suspended effects.
package approval

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

type mockEngine struct {
	mu              sync.Mutex
	approved        []string
	rejected        []string
	lastRejectActor string
	commitCalls     []protocol.CommitRequest
	failCalls       []protocol.FailRequest
	pending         []protocol.ApprovalRecord
	listErr         map[string]error
}

// Intercept is a no-op stub used to satisfy the engine interface.
func (m *mockEngine) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	return protocol.InterceptResponse{}, nil
}

// Commit records the successful execution reported to the engine.
func (m *mockEngine) Commit(ctx context.Context, req protocol.CommitRequest) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.commitCalls = append(m.commitCalls, req)
	return nil
}

// Fail records the failed execution reported to the engine.
func (m *mockEngine) Fail(ctx context.Context, req protocol.FailRequest) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.failCalls = append(m.failCalls, req)
	return nil
}

// Approve records that the approval was resumed and returns a valid response.
func (m *mockEngine) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
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
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rejected = append(m.rejected, req.ApprovalID)
	m.lastRejectActor = req.Actor
	return nil
}

// ListPendingApprovals returns the configured pending records, or the
// configured per-organization error when present.
func (m *mockEngine) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.listErr[req.OrgID]; err != nil {
		return protocol.ListPendingApprovalsResponse{}, err
	}
	return protocol.ListPendingApprovalsResponse{Records: m.pending}, nil
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
	handler := NewHandler(store, engine, mockExec, broadcaster, 0, 1<<20, nil)

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

// TestRejectForwardsActor verifies the reject actor reaches the engine audit trail.
func TestRejectForwardsActor(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	handler := NewHandler(store, engine, nil, nil, 0, 1<<20, nil)
	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{"id":"u1"}`))

	req := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/reject", bytes.NewBufferString(`{"actor":"alice"}`))
	req.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.RejectApproval(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("reject status: %d", w.Code)
	}
	if engine.lastRejectActor != "alice" {
		t.Fatalf("expected actor alice, got %q", engine.lastRejectActor)
	}
}

// TestRejectDefaultsActorToUnknown verifies an empty actor body still sends a value.
func TestRejectDefaultsActorToUnknown(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	handler := NewHandler(store, engine, nil, nil, 0, 1<<20, nil)
	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{"id":"u1"}`))

	req := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/reject", nil)
	req.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.RejectApproval(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("reject status: %d", w.Code)
	}
	if engine.lastRejectActor != "unknown" {
		t.Fatalf("expected actor unknown, got %q", engine.lastRejectActor)
	}
}

// TestMalformedDecisionBodyReturns400 verifies bad JSON is not swallowed.
func TestMalformedDecisionBodyReturns400(t *testing.T) {
	for _, action := range []struct {
		name   string
		call   func(*Handler, http.ResponseWriter, *http.Request)
		verify func(*testing.T, *mockEngine, Record)
	}{
		{
			name: "approve",
			call: (*Handler).ApproveApproval,
			verify: func(t *testing.T, engine *mockEngine, rec Record) {
				if len(engine.approved) != 0 {
					t.Fatalf("approve must not be forwarded on a malformed body: %+v", engine.approved)
				}
			},
		},
		{
			name: "reject",
			call: (*Handler).RejectApproval,
			verify: func(t *testing.T, engine *mockEngine, rec Record) {
				if len(engine.rejected) != 0 {
					t.Fatalf("reject must not be forwarded on a malformed body: %+v", engine.rejected)
				}
			},
		},
	} {
		t.Run(action.name, func(t *testing.T) {
			store := NewStore()
			engine := &mockEngine{}
			handler := NewHandler(store, engine, nil, nil, 0, 1<<20, nil)
			rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{"id":"u1"}`))

			req := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/"+action.name, bytes.NewBufferString(`{"actor":`))
			req.Header.Set("X-Org-Id", "org-1")
			w := httptest.NewRecorder()
			action.call(handler, w, req)

			if w.Code != http.StatusBadRequest {
				t.Fatalf("expected 400, got %d", w.Code)
			}
			got, ok := store.Get(rec.ID)
			if !ok || got.Status != StatusPending {
				t.Fatalf("record must stay pending after a malformed body, got %+v", got)
			}
			action.verify(t, engine, rec)
		})
	}
}

// TestConcurrentDoubleApproveReturns409 verifies the loser reports a conflict.
func TestConcurrentDoubleApproveReturns409(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	mockExec := func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{Success: true, Output: []byte(`{"deleted":true}`)}, nil
	}
	handler := NewHandler(store, engine, mockExec, nil, 0, 1<<20, nil)
	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{"id":"u1"}`))

	const workers = 8
	start := make(chan struct{})
	codes := make(chan int, workers)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			req := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/approve", nil)
			req.Header.Set("X-Org-Id", "org-1")
			w := httptest.NewRecorder()
			<-start
			handler.ApproveApproval(w, req)
			codes <- w.Code
		}()
	}
	close(start)
	wg.Wait()
	close(codes)

	conflicts := 0
	successes := 0
	for code := range codes {
		switch code {
		case http.StatusOK:
			successes++
		case http.StatusConflict:
			conflicts++
		default:
			t.Fatalf("unexpected status: %d", code)
		}
	}
	if successes != 1 {
		t.Fatalf("expected exactly one success, got %d", successes)
	}
	if conflicts != workers-1 {
		t.Fatalf("expected %d conflicts, got %d", workers-1, conflicts)
	}
	if len(engine.approved) != 1 {
		t.Fatalf("engine approve must run exactly once, got %+v", engine.approved)
	}
}

// TestPostApprovalExecutionFailureUsesFail verifies the failure path records a Fail.
func TestPostApprovalExecutionFailureUsesFail(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	mockExec := func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, errors.New("upstream exploded")
	}
	handler := NewHandler(store, engine, mockExec, nil, 0, 1<<20, nil)
	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{"id":"u1"}`))

	req := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/approve", nil)
	req.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.ApproveApproval(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("approve status: %d", w.Code)
	}
	if len(engine.failCalls) != 1 {
		t.Fatalf("expected one Fail call, got %+v", engine.failCalls)
	}
	if engine.failCalls[0].EffectID != "eff-1" || engine.failCalls[0].Error != "upstream exploded" {
		t.Fatalf("unexpected fail call: %+v", engine.failCalls[0])
	}
	if len(engine.commitCalls) != 0 {
		t.Fatalf("a failed execution must not be committed, got %+v", engine.commitCalls)
	}
}

// TestListApprovalsOrderedAndLimited verifies the handler returns newest-first
// records bounded by the limit query parameter.
func TestListApprovalsOrderedAndLimited(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	handler := NewHandler(store, engine, nil, nil, 0, 1<<20, nil)

	now := time.Now().UTC()
	store.Create(Record{ID: "oldest", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: now.Add(-2 * time.Hour)})
	store.Create(Record{ID: "middle", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: now.Add(-1 * time.Hour)})
	store.Create(Record{ID: "newest", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: now})

	req := httptest.NewRequest(http.MethodGet, "/approvals?state=pending&limit=2", nil)
	req.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.ListApprovals(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("list status: %d", w.Code)
	}
	var listed []Record
	if err := json.Unmarshal(w.Body.Bytes(), &listed); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	if len(listed) != 2 {
		t.Fatalf("expected 2 records, got %d", len(listed))
	}
	if listed[0].ID != "newest" || listed[1].ID != "middle" {
		t.Fatalf("expected newest then middle, got %s then %s", listed[0].ID, listed[1].ID)
	}
}

// TestApprovalMetricsRecorded verifies decision outcomes and latency are
// published through the shared registry.
func TestApprovalMetricsRecorded(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	registry := metrics.NewRegistry()
	handler := NewHandler(store, engine, nil, nil, 0, 1<<20, nil)
	handler.SetMetrics(registry)

	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{}`))

	rej := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/reject", bytes.NewBufferString(`{"actor":"alice"}`))
	rej.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.RejectApproval(w, rej)
	if w.Code != http.StatusOK {
		t.Fatalf("reject status: %d", w.Code)
	}

	// A second decision on the same record must hit the conflict path.
	rej2 := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/reject", bytes.NewBufferString(`{}`))
	rej2.Header.Set("X-Org-Id", "org-1")
	w2 := httptest.NewRecorder()
	handler.RejectApproval(w2, rej2)
	if w2.Code != http.StatusConflict {
		t.Fatalf("expected 409 on double decision, got %d", w2.Code)
	}

	// A decision that never reaches the state machine must not add a latency
	// sample, so the duration histogram only covers genuine decision attempts.
	nf := httptest.NewRequest(http.MethodPost, "/approvals/does-not-exist/approve", bytes.NewBufferString(`{}`))
	nf.Header.Set("X-Org-Id", "org-1")
	nfw := httptest.NewRecorder()
	handler.ApproveApproval(nfw, nf)
	if nfw.Code != http.StatusNotFound {
		t.Fatalf("expected 404 on unknown approval, got %d", nfw.Code)
	}

	rendered := registry.Render()
	for _, want := range []string{
		`undolog_proxy_approval_decisions_total{action="reject",result="applied"} 1`,
		`undolog_proxy_approval_decisions_total{action="reject",result="conflict"} 1`,
		`undolog_proxy_approval_decision_duration_seconds_count{action="reject"} 2`,
	} {
		if !strings.Contains(rendered, want) {
			t.Errorf("expected metrics to contain %q, got:\n%s", want, rendered)
		}
	}
}

// TestDecisionBodySizeLimit verifies an oversized decision body returns 413
// before the record is mutated, so it cannot flip a pending approval.
func TestDecisionBodySizeLimit(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{}
	handler := NewHandler(store, engine, nil, nil, 0, 64, nil)
	rec := handler.CreatePending("org-1", "sess-1", "eff-1", "delete_user", []byte(`{}`))

	big := strings.Repeat("x", 4096)
	req := httptest.NewRequest(http.MethodPost, "/approvals/"+rec.ID+"/reject", bytes.NewBufferString(`{"actor":"`+big+`"}`))
	req.Header.Set("X-Org-Id", "org-1")
	w := httptest.NewRecorder()
	handler.RejectApproval(w, req)

	if w.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected 413 for oversized decision body, got %d", w.Code)
	}
	if got, ok := store.Get(rec.ID); !ok || got.Status != StatusPending {
		t.Fatalf("an oversized body must not mutate the record, got %+v", got)
	}
}
