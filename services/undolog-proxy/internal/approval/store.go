// Package approval stores and resolves approval requests for irreversible calls.
//
// It keeps the approval state machine in-memory for the proxy runtime and
// records the full lifecycle of each pending effect decision.
package approval

import (
	"crypto/rand"
	"encoding/hex"
	"sort"
	"sync"
	"time"
)

// Status represents the lifecycle state of an approval request.
type Status string

const (
	// StatusPending marks an approval request that still needs a decision.
	StatusPending Status = "pending"
	// StatusApproved marks an approval request that was allowed to continue.
	StatusApproved Status = "approved"
	// StatusRejected marks an approval request that was denied.
	StatusRejected Status = "rejected"
)

// Record captures one approval request and its resolution state.
type Record struct {
	// ID is the stable approval identifier.
	ID string `json:"id"`
	// OrgID identifies the owning organization.
	OrgID string `json:"org_id"`
	// SessionID identifies the affected agent session.
	SessionID string `json:"session_id"`
	// EffectID identifies the effect that is waiting for approval.
	EffectID string `json:"effect_id"`
	// ToolName is the intercepted tool name.
	ToolName string `json:"tool_name"`
	// Args stores the canonical call arguments.
	Args []byte `json:"args"`
	// Status indicates whether the request is pending, approved, or rejected.
	Status Status `json:"status"`
	// CreatedAt records when the request was created.
	CreatedAt time.Time `json:"created_at"`
	// ResolvedAt records when a decision was made.
	ResolvedAt *time.Time `json:"resolved_at,omitempty"`
}

// Store is an in-memory approval repository used by the proxy service.
type Store struct {
	mu      sync.RWMutex
	records map[string]Record
}

// NewStore creates an empty approval store.
func NewStore() *Store {
	return &Store{records: make(map[string]Record)}
}

// NewID generates a UUID-like identifier for a new approval record.
func NewID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	buf := make([]byte, 32)
	hex.Encode(buf, b[:])
	return string(buf)
}

// Create inserts a new approval record and returns the stored copy.
func (s *Store) Create(rec Record) Record {
	if rec.ID == "" {
		rec.ID = NewID()
	}
	if rec.CreatedAt.IsZero() {
		rec.CreatedAt = time.Now().UTC()
	}
	if rec.Status == "" {
		rec.Status = StatusPending
	}
	s.mu.Lock()
	s.records[rec.ID] = rec
	s.mu.Unlock()
	return rec
}

// List returns approval records filtered by organization and status, ordered
// by creation time descending (newest first) with the record ID as a
// deterministic tiebreaker. When limit is positive, at most that many records
// are returned.
func (s *Store) List(orgID string, status Status, limit int) []Record {
	s.mu.RLock()
	recs := make([]Record, 0, len(s.records))
	for key := range s.records {
		rec := s.records[key]
		if orgID != "" && rec.OrgID != orgID {
			continue
		}
		if status != "" && rec.Status != status {
			continue
		}
		recs = append(recs, rec)
	}
	s.mu.RUnlock()

	sort.Slice(recs, func(i, j int) bool {
		if recs[i].CreatedAt.Equal(recs[j].CreatedAt) {
			return recs[i].ID < recs[j].ID
		}
		return recs[i].CreatedAt.After(recs[j].CreatedAt)
	})
	if limit > 0 && len(recs) > limit {
		recs = recs[:limit]
	}
	return recs
}

// Get looks up an approval record by ID.
func (s *Store) Get(id string) (Record, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rec, ok := s.records[id]
	return rec, ok
}

// CompareAndSwap atomically transitions a record from `from` to `to` only when
// its current status equals `from`, stamping the resolution time. It returns
// the stored copy and whether the transition happened. This closes the
// check-then-act window so concurrent decisions resolve exactly one winner and
// the loser is reported as a conflict without an engine round trip.
func (s *Store) CompareAndSwap(id string, from, to Status) (Record, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, ok := s.records[id]
	if !ok || rec.Status != from {
		return Record{}, false
	}
	rec.Status = to
	now := time.Now().UTC()
	rec.ResolvedAt = &now
	s.records[id] = rec
	return rec, true
}

// RestorePending reverts a terminal decision back to pending and clears the
// resolution timestamp. The decision handler calls this when the engine round
// trip fails so the approval remains resolvable on a later attempt.
func (s *Store) RestorePending(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, ok := s.records[id]
	if !ok || rec.Status == StatusPending {
		return
	}
	rec.Status = StatusPending
	rec.ResolvedAt = nil
	s.records[id] = rec
}

// UpsertPending inserts a pending record or refreshes an existing one in place.
// The reconciler uses it to restore the proxy's approval view from the engine;
// it never downgrades an already-resolved local record back to pending.
func (s *Store) UpsertPending(rec Record) {
	if rec.ID == "" {
		rec.ID = NewID()
	}
	if rec.CreatedAt.IsZero() {
		rec.CreatedAt = time.Now().UTC()
	}
	if rec.Status == "" {
		rec.Status = StatusPending
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if existing, ok := s.records[rec.ID]; ok && existing.Status != StatusPending {
		return
	}
	s.records[rec.ID] = rec
}

// Sweep removes records that reached their retention age: terminal records are
// aged from ResolvedAt and stale pending records from CreatedAt. Records in
// activeIDs were just confirmed as pending by the engine and are kept so a
// still-decidable approval never flickers out of the dashboard. Records whose
// organization failed to reconcile this cycle are also kept, because their
// current status is unknown and sweeping them could drop an approval a human
// may still decide on. It returns the number of records removed and keeps the
// in-memory store bounded.
func (s *Store) Sweep(retainedBefore time.Time, activeIDs, failedOrgs map[string]struct{}) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	removed := 0
	for id := range s.records {
		rec := s.records[id]
		if _, active := activeIDs[id]; active {
			continue
		}
		if _, failed := failedOrgs[rec.OrgID]; failed {
			continue
		}
		cutoff := rec.CreatedAt
		if rec.Status != StatusPending && rec.ResolvedAt != nil {
			cutoff = *rec.ResolvedAt
		}
		if cutoff.Before(retainedBefore) {
			delete(s.records, id)
			removed++
		}
	}
	return removed
}
