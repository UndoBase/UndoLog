// Package approval tests the in-memory approval repository.
//
// These tests cover the atomic decision transition, deterministic listing, the
// reconciliation upsert, and the retention sweep that keeps the store bounded.
package approval

import (
	"sync"
	"testing"
	"time"
)

func testRecord(id, orgID string, createdAt time.Time) Record {
	return Record{
		ID:        id,
		OrgID:     orgID,
		SessionID: "sess-1",
		EffectID:  "eff-1",
		ToolName:  "delete_user",
		Status:    StatusPending,
		CreatedAt: createdAt,
	}
}

// TestStoreListSortsNewestFirst verifies deterministic ordering by creation time.
func TestStoreListSortsNewestFirst(t *testing.T) {
	store := NewStore()
	now := time.Now().UTC()
	older := store.Create(testRecord("a", "org-1", now.Add(-2*time.Hour)))
	middle := store.Create(testRecord("b", "org-1", now.Add(-1*time.Hour)))
	newer := store.Create(testRecord("c", "org-1", now))

	got := store.List("org-1", "", 0)
	if len(got) != 3 {
		t.Fatalf("expected 3 records, got %d", len(got))
	}
	want := []string{newer.ID, middle.ID, older.ID}
	for i, id := range want {
		if got[i].ID != id {
			t.Fatalf("record %d: expected %s, got %s", i, id, got[i].ID)
		}
	}
}

// TestStoreListTiebreakById verifies records sharing a timestamp order by ID.
func TestStoreListTiebreakById(t *testing.T) {
	store := NewStore()
	at := time.Now().UTC()
	store.Create(testRecord("b-id", "org-1", at))
	store.Create(testRecord("a-id", "org-1", at))

	got := store.List("org-1", "", 0)
	if got[0].ID != "a-id" || got[1].ID != "b-id" {
		t.Fatalf("expected a-id then b-id, got %s then %s", got[0].ID, got[1].ID)
	}
}

// TestStoreListLimit verifies the limit caps the returned records.
func TestStoreListLimit(t *testing.T) {
	store := NewStore()
	now := time.Now().UTC()
	for i := 0; i < 5; i++ {
		store.Create(testRecord(string(rune('a'+i)), "org-1", now.Add(-time.Duration(i)*time.Minute)))
	}

	got := store.List("org-1", "", 2)
	if len(got) != 2 {
		t.Fatalf("expected 2 records, got %d", len(got))
	}
}

// TestStoreCompareAndSwap verifies only a pending record can be resolved once.
func TestStoreCompareAndSwap(t *testing.T) {
	store := NewStore()
	rec := store.Create(testRecord("approval-1", "org-1", time.Now().UTC()))

	resolved, ok := store.CompareAndSwap(rec.ID, StatusPending, StatusApproved)
	if !ok {
		t.Fatal("expected the first decision to win")
	}
	if resolved.Status != StatusApproved || resolved.ResolvedAt == nil {
		t.Fatalf("expected approved with resolved timestamp, got %+v", resolved)
	}

	if _, ok := store.CompareAndSwap(rec.ID, StatusPending, StatusRejected); ok {
		t.Fatal("a second decision on the same approval must lose the CAS")
	}
}

// TestStoreCompareAndSwapConcurrent verifies exactly one winner under contention.
func TestStoreCompareAndSwapConcurrent(t *testing.T) {
	store := NewStore()
	rec := store.Create(testRecord("approval-1", "org-1", time.Now().UTC()))

	const workers = 16
	start := make(chan struct{})
	winners := make(chan bool, workers)
	var wg sync.WaitGroup
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			_, ok := store.CompareAndSwap(rec.ID, StatusPending, StatusApproved)
			winners <- ok
		}()
	}
	close(start)
	wg.Wait()
	close(winners)

	winCount := 0
	for ok := range winners {
		if ok {
			winCount++
		}
	}
	if winCount != 1 {
		t.Fatalf("expected exactly one CAS winner, got %d", winCount)
	}
}

// TestStoreRestorePending verifies an engine failure rolls the decision back.
func TestStoreRestorePending(t *testing.T) {
	store := NewStore()
	rec := store.Create(testRecord("approval-1", "org-1", time.Now().UTC()))
	store.CompareAndSwap(rec.ID, StatusPending, StatusApproved)

	store.RestorePending(rec.ID)

	got, ok := store.Get(rec.ID)
	if !ok {
		t.Fatal("record vanished after restore")
	}
	if got.Status != StatusPending || got.ResolvedAt != nil {
		t.Fatalf("expected pending with no resolved time, got %+v", got)
	}
}

// TestStoreSweep verifies the retention sweep removes aged terminal and stale
// pending records but keeps records confirmed as still pending.
func TestStoreSweep(t *testing.T) {
	store := NewStore()
	now := time.Now().UTC()

	freshPending := store.Create(testRecord("fresh-pending", "org-1", now.Add(-time.Minute)))
	oldPending := store.Create(testRecord("old-pending", "org-1", now.Add(-2*time.Hour)))

	oldResolved := store.Create(testRecord("old-resolved", "org-1", now.Add(-2*time.Hour)))
	oldResolved.Status = StatusApproved
	resolvedAt := now.Add(-2 * time.Hour)
	oldResolved.ResolvedAt = &resolvedAt
	store.UpsertPending(oldResolved)

	freshResolved := store.Create(testRecord("fresh-resolved", "org-1", now.Add(-time.Minute)))
	freshResolved.Status = StatusRejected
	freshResolvedAt := now.Add(-time.Minute)
	freshResolved.ResolvedAt = &freshResolvedAt
	store.UpsertPending(freshResolved)

	activePending := store.Create(testRecord("active-pending", "org-1", now.Add(-2*time.Hour)))

	removed := store.Sweep(now.Add(-1*time.Hour), map[string]struct{}{freshPending.ID: {}, activePending.ID: {}}, nil)

	if removed != 2 {
		t.Fatalf("expected 2 removed, got %d", removed)
	}
	if _, ok := store.Get(oldPending.ID); ok {
		t.Fatal("stale old pending record should have been swept")
	}
	if _, ok := store.Get(oldResolved.ID); ok {
		t.Fatal("old resolved record should have been swept")
	}
	if _, ok := store.Get(freshPending.ID); !ok {
		t.Fatal("fresh pending record should survive")
	}
	if _, ok := store.Get(freshResolved.ID); !ok {
		t.Fatal("fresh resolved record should survive")
	}
	if _, ok := store.Get(activePending.ID); !ok {
		t.Fatal("still-pending record confirmed by the engine must survive")
	}
}

// TestStoreSweepKeepsActivePending verifies a still-pending record past the
// retention age is not removed when the engine still reports it as pending.
func TestStoreSweepKeepsActivePending(t *testing.T) {
	store := NewStore()
	now := time.Now().UTC()
	rec := store.Create(testRecord("pending-old", "org-1", now.Add(-48*time.Hour)))

	removed := store.Sweep(now.Add(-24*time.Hour), map[string]struct{}{rec.ID: {}}, nil)
	if removed != 0 {
		t.Fatalf("expected 0 removed for an active pending record, got %d", removed)
	}
	if _, ok := store.Get(rec.ID); !ok {
		t.Fatal("active pending record must be kept for a human decision")
	}
}

// TestStoreUpsertPendingNeverDowngrades verifies reconcile cannot reopen a decision.
func TestStoreUpsertPendingNeverDowngrades(t *testing.T) {
	store := NewStore()
	rec := store.Create(testRecord("approval-1", "org-1", time.Now().UTC()))
	store.CompareAndSwap(rec.ID, StatusPending, StatusApproved)

	store.UpsertPending(testRecord("approval-1", "org-1", time.Now().UTC()))

	got, ok := store.Get(rec.ID)
	if !ok {
		t.Fatal("record vanished")
	}
	if got.Status != StatusApproved {
		t.Fatalf("reconcile must not downgrade a resolved record, got %s", got.Status)
	}
}
