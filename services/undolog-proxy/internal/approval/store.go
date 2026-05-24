// Package approval stores and resolves approval requests for irreversible calls.
//
// It keeps the approval state machine in-memory for the proxy runtime and
// records the full lifecycle of each pending effect decision.
package approval

import (
	"crypto/rand"
	"encoding/hex"
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

// List returns approval records filtered by organization and status.
func (s *Store) List(orgID string, status Status) []Record {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Record, 0, len(s.records))
	for key := range s.records {
		rec := s.records[key]
		if orgID != "" && rec.OrgID != orgID {
			continue
		}
		if status != "" && rec.Status != status {
			continue
		}
		out = append(out, rec)
	}
	return out
}

// Get looks up an approval record by ID.
func (s *Store) Get(id string) (Record, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rec, ok := s.records[id]
	return rec, ok
}

// UpdateStatus changes the record status and stamps the resolution time.
func (s *Store) UpdateStatus(id string, status Status) (Record, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, ok := s.records[id]
	if !ok {
		return Record{}, false
	}
	// Terminal decisions always stamp a resolved timestamp exactly once.
	rec.Status = status
	now := time.Now().UTC()
	rec.ResolvedAt = &now
	s.records[id] = rec
	return rec, true
}
