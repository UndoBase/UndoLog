// Package sse fan-outs proxy events to dashboard clients over Server-Sent Events.
//
// It keeps dashboard subscriptions org-scoped so the UI only receives events
// for the tenant that owns the intercepted session.
package sse

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// EventType identifies a proxy lifecycle event in the dashboard stream.
type EventType string

const (
	// EventEffectIntercepted marks when a call reaches the engine.
	EventEffectIntercepted EventType = "effect_intercepted"
	// EventEffectExecuted marks a tool call that ran upstream.
	EventEffectExecuted EventType = "effect_executed"
	// EventEffectCommitted marks a successful commit in the engine.
	EventEffectCommitted EventType = "effect_committed"
	// EventEffectReplayed marks a call served from cached state.
	EventEffectReplayed EventType = "effect_replayed"
	// EventEffectFailed marks a failure in any interception stage.
	EventEffectFailed EventType = "effect_failed"
	// EventApprovalRequired marks a call waiting for human approval.
	EventApprovalRequired EventType = "approval_required"
	// EventApprovalApproved marks a human-approved call resuming execution.
	EventApprovalApproved EventType = "approval_approved"
	// EventApprovalRejected marks a human-rejected call that stops here.
	EventApprovalRejected EventType = "approval_rejected"
)

// Event is the SSE payload delivered to dashboard subscribers.
type Event struct {
	// Type classifies the event.
	Type EventType `json:"type"`
	// Timestamp records when the event was emitted.
	Timestamp time.Time `json:"timestamp"`
	// OrgID scopes the event to one tenant.
	OrgID string `json:"org_id"`
	// SessionID identifies the affected session.
	SessionID string `json:"session_id,omitempty"`
	// EffectID identifies the affected effect.
	EffectID string `json:"effect_id,omitempty"`
	// ApprovalID identifies the affected approval request.
	ApprovalID string `json:"approval_id,omitempty"`
	// Payload carries event-specific details.
	Payload json.RawMessage `json:"payload,omitempty"`
}

// Broadcaster maintains org-scoped subscriber lists for SSE delivery.
type Broadcaster struct {
	bufferSize int
	mu         sync.RWMutex
	subs       map[string]map[uint64]chan Event
	nextID     atomic.Uint64
}

// NewBroadcaster creates a broadcaster with the given channel buffer size.
func NewBroadcaster(bufferSize int) *Broadcaster {
	if bufferSize <= 0 {
		bufferSize = 128
	}
	return &Broadcaster{
		bufferSize: bufferSize,
		subs:       make(map[string]map[uint64]chan Event),
	}
}

// Emit sends one event to every subscriber registered for the matching org.
func (b *Broadcaster) Emit(evt Event) {
	if evt.OrgID == "" {
		return
	}
	evt.Timestamp = evt.Timestamp.UTC()
	if evt.Timestamp.IsZero() {
		evt.Timestamp = time.Now().UTC()
	}

	b.mu.RLock()
	defer b.mu.RUnlock()

	// Drop events rather than blocking the proxy hot path.
	for _, ch := range b.subs[evt.OrgID] {
		select {
		case ch <- evt:
		default:
		}
	}
}

// Subscribe registers one org-scoped SSE subscriber and returns an unsubscribe function.
func (b *Broadcaster) Subscribe(orgID string) (<-chan Event, func()) {
	ch := make(chan Event, b.bufferSize)
	id := b.nextID.Add(1)

	b.mu.Lock()
	if b.subs[orgID] == nil {
		b.subs[orgID] = make(map[uint64]chan Event)
	}
	b.subs[orgID][id] = ch
	b.mu.Unlock()

	unsubscribe := func() {
		b.mu.Lock()
		if orgSubs, ok := b.subs[orgID]; ok {
			if sub, ok := orgSubs[id]; ok {
				delete(orgSubs, id)
				close(sub)
			}
			if len(orgSubs) == 0 {
				delete(b.subs, orgID)
			}
		}
		b.mu.Unlock()
	}

	return ch, unsubscribe
}

// Handler serves the SSE endpoint used by the dashboard.
func (b *Broadcaster) Handler(w http.ResponseWriter, r *http.Request) {
	orgID := strings.TrimSpace(r.Header.Get("X-Org-Id"))
	if orgID == "" {
		orgID = strings.TrimSpace(r.URL.Query().Get("org_id"))
	}
	if orgID == "" {
		http.Error(w, "org_id required", http.StatusUnauthorized)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	events, unsubscribe := b.Subscribe(orgID)
	defer unsubscribe()

	heartbeat := time.NewTicker(25 * time.Second)
	defer heartbeat.Stop()

	for {
		select {
		case evt, ok := <-events:
			if !ok {
				return
			}
			if err := writeEvent(w, evt); err != nil {
				return
			}
			flusher.Flush()
		case <-heartbeat.C:
			_, _ = fmt.Fprint(w, ": ping\n\n")
			flusher.Flush()
		case <-r.Context().Done():
			return
		}
	}
}

func writeEvent(w http.ResponseWriter, evt Event) error {
	payload, err := json.Marshal(evt)
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "event: %s\n", evt.Type); err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "id: %s\n", strconv.FormatInt(evt.Timestamp.UnixNano(), 10)); err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "data: %s\n\n", payload); err != nil {
		return err
	}
	return nil
}

// Close drains and closes all subscriber channels, releasing broadcaster resources.
func (b *Broadcaster) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	for orgID, subs := range b.subs {
		for id, ch := range subs {
			close(ch)
			delete(subs, id)
		}
		delete(b.subs, orgID)
	}
}

// ServeHTTP allows Broadcaster to satisfy http.Handler.
func (b *Broadcaster) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	b.Handler(w, r)
}
