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

	"undolog-proxy/internal/metrics"
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
	metrics    *metrics.Registry
}

// Metric names published by the broadcaster.
const (
	sseSubscribersMetric   = "undolog_proxy_sse_subscribers"
	sseEventsDroppedMetric = "undolog_proxy_sse_events_dropped_total"
)

// SetMetrics wires the registry so subscriber counts and dropped events are
// exposed on /metrics. A nil registry disables instrumentation.
func (b *Broadcaster) SetMetrics(reg *metrics.Registry) {
	b.metrics = reg
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
	dropped := 0
	for _, ch := range b.subs[evt.OrgID] {
		select {
		case ch <- evt:
		default:
			dropped++
		}
	}
	if dropped > 0 {
		b.setDroppedMetric(evt.OrgID, dropped)
	}
}

// setDroppedMetric records a dropped-event count for one org.
func (b *Broadcaster) setDroppedMetric(org string, n int) {
	if b.metrics == nil {
		return
	}
	b.metrics.Counter(sseEventsDroppedMetric, "SSE events dropped because a subscriber channel was full", "org").
		Add(float64(n), org)
}

// recordSubscriberMetric publishes the active subscriber count for one org.
func (b *Broadcaster) recordSubscriberMetric(org string, count int) {
	if b.metrics == nil {
		return
	}
	b.metrics.Gauge(sseSubscribersMetric, "Active SSE dashboard subscribers", "org").Set(float64(count), org)
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
	b.recordSubscriberMetric(orgID, len(b.subs[orgID]))
	b.mu.Unlock()

	unsubscribe := func() {
		b.mu.Lock()
		if orgSubs, ok := b.subs[orgID]; ok {
			if sub, ok := orgSubs[id]; ok {
				delete(orgSubs, id)
				close(sub)
			}
			b.recordSubscriberMetric(orgID, len(orgSubs))
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

	// SSE streams must outlive the server WriteTimeout, which would otherwise
	// terminate the connection while the dashboard idles between heartbeats.
	// Clearing the deadline is best-effort: writers without a deadline concept
	// (e.g. httptest.ResponseRecorder) report ErrNotSupported, which is a no-op.
	_ = http.NewResponseController(w).SetWriteDeadline(time.Time{})

	// Flush the headers so the client observes the stream as open before the
	// first event or heartbeat arrives.
	flusher.Flush()
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
			// A failed heartbeat means the connection is gone (e.g. a silent
			// network drop), so end the handler instead of idling forever.
			if _, err := fmt.Fprint(w, ": ping\n\n"); err != nil {
				return
			}
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
		b.recordSubscriberMetric(orgID, 0)
		delete(b.subs, orgID)
	}
}

// ServeHTTP allows Broadcaster to satisfy http.Handler.
func (b *Broadcaster) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	b.Handler(w, r)
}
