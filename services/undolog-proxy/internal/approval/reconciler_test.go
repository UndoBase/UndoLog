// Package approval tests the reconciler that keeps the proxy's approval view
// in sync with the engine.
package approval

import (
	"context"
	"errors"
	"testing"
	"time"

	"undolog-proxy/internal/protocol"
)

// TestReconcileApprovalsRestoresPending verifies pending approvals from the
// engine are upserted into the store so a proxy restart does not orphan them.
func TestReconcileApprovalsRestoresPending(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{
		pending: []protocol.ApprovalRecord{
			{ApprovalID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{"id":"u1"}`), CreatedAtUnix: 1700000000000},
		},
	}

	ReconcileApprovals(context.Background(), engine, store, []string{"org-1"}, nil)

	rec, ok := store.Get("ap-1")
	if !ok {
		t.Fatal("pending approval was not reconciled into the store")
	}
	if rec.OrgID != "org-1" || rec.SessionID != "s-1" || rec.EffectID != "e-1" || rec.ToolName != "delete_user" {
		t.Fatalf("unexpected reconciled record: %+v", rec)
	}
	if string(rec.Args) != `{"id":"u1"}` {
		t.Fatalf("unexpected args: %s", rec.Args)
	}
	wantCreated := time.UnixMilli(1700000000000).UTC()
	if !rec.CreatedAt.Equal(wantCreated) {
		t.Fatalf("expected created at %s, got %s", wantCreated, rec.CreatedAt)
	}
}

// TestReconcileApprovalsDoesNotDowngradeResolved verifies a locally resolved
// record is not reopened by an engine snapshot.
func TestReconcileApprovalsDoesNotDowngradeResolved(t *testing.T) {
	store := NewStore()
	rec := store.Create(Record{ID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: time.Now().UTC()})
	store.CompareAndSwap(rec.ID, StatusPending, StatusApproved)

	engine := &mockEngine{
		pending: []protocol.ApprovalRecord{
			{ApprovalID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), CreatedAtUnix: time.Now().UnixMilli()},
		},
	}

	ReconcileApprovals(context.Background(), engine, store, []string{"org-1"}, nil)

	got, ok := store.Get("ap-1")
	if !ok {
		t.Fatal("record vanished")
	}
	if got.Status != StatusApproved {
		t.Fatalf("reconcile must not downgrade a resolved record, got %s", got.Status)
	}
}

// TestReconcileApprovalsSweepSkipsFailedOrg verifies the sweep never removes
// records of an organization whose reconcile failed: their status is unknown, so
// sweeping them could drop an approval a human may still decide on.
func TestReconcileApprovalsSweepSkipsFailedOrg(t *testing.T) {
	store := NewStore()
	now := time.Now().UTC()
	rec := store.Create(Record{ID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: now.Add(-48 * time.Hour)})

	engine := &mockEngine{
		listErr: map[string]error{"org-1": errors.New("engine unreachable")},
	}

	active, failedOrgs := ReconcileApprovals(context.Background(), engine, store, []string{"org-1"}, nil)
	if len(active) != 0 {
		t.Fatalf("expected no confirmed-active records, got %d", len(active))
	}
	if len(failedOrgs) != 1 {
		t.Fatalf("expected org-1 to be marked failed, got %v", failedOrgs)
	}

	removed := store.Sweep(now.Add(-24*time.Hour), active, failedOrgs)
	if removed != 0 {
		t.Fatalf("records of a failed org must not be swept, got %d removed", removed)
	}
	if _, ok := store.Get(rec.ID); !ok {
		t.Fatal("pending record of a failed org must survive the sweep")
	}
}

// TestRunApprovalReconcilerSweeps verifies the periodic loop reconciles pending
// approvals, sweeps stale records past retention, and keeps records the engine
// still reports as pending.
func TestRunApprovalReconcilerSweeps(t *testing.T) {
	store := NewStore()
	now := time.Now().UTC()
	// Stale pending record: older than retention, no longer in the engine.
	store.Create(Record{ID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: now.Add(-48 * time.Hour)})
	// Old pending record that the engine still holds must survive the sweep.
	store.Create(Record{ID: "ap-3", OrgID: "org-1", SessionID: "s-3", EffectID: "e-3", ToolName: "delete_user", Args: []byte(`{}`), Status: StatusPending, CreatedAt: now.Add(-48 * time.Hour)})

	engine := &mockEngine{
		pending: []protocol.ApprovalRecord{
			{ApprovalID: "ap-2", OrgID: "org-1", SessionID: "s-2", EffectID: "e-2", ToolName: "delete_user", Args: []byte(`{}`), CreatedAtUnix: now.Add(-2 * time.Minute).UnixMilli()},
			{ApprovalID: "ap-3", OrgID: "org-1", SessionID: "s-3", EffectID: "e-3", ToolName: "delete_user", Args: []byte(`{}`), CreatedAtUnix: now.Add(-48 * time.Hour).UnixMilli()},
		},
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		RunApprovalReconciler(ctx, engine, store, []string{"org-1"}, 20*time.Millisecond, 24*time.Hour, nil)
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, ok := store.Get("ap-2"); ok {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	<-done

	if _, ok := store.Get("ap-2"); !ok {
		t.Fatal("pending record from engine was not reconciled")
	}
	if _, ok := store.Get("ap-1"); ok {
		t.Fatal("stale record past the retention window should have been swept")
	}
	if _, ok := store.Get("ap-3"); !ok {
		t.Fatal("old pending record still held by the engine must be kept")
	}
}

// TestRunApprovalReconcilerNonPositiveInterval verifies a non-positive interval
// does not panic time.NewTicker: the loop falls back to the default interval and
// still reconciles immediately on entry.
func TestRunApprovalReconcilerNonPositiveInterval(t *testing.T) {
	store := NewStore()
	engine := &mockEngine{
		pending: []protocol.ApprovalRecord{
			{ApprovalID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", Args: []byte(`{}`), CreatedAtUnix: time.Now().UnixMilli()},
		},
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		RunApprovalReconciler(ctx, engine, store, []string{"org-1"}, -1, 24*time.Hour, nil)
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, ok := store.Get("ap-1"); ok {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	<-done

	if _, ok := store.Get("ap-1"); !ok {
		t.Fatal("reconciler did not reconcile with a defaulted interval")
	}
}
