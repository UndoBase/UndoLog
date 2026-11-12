// Package sse fan-outs proxy events to dashboard clients over Server-Sent Events.
//
// It keeps dashboard subscriptions org-scoped so the UI only receives events
// for the tenant that owns the intercepted session.
package sse

import (
	"encoding/json"
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
	// EventResyncRequired marks when an event gap exceeds buffer capacity.
	EventResyncRequired EventType = "resync_required"
)

// Event is the SSE payload delivered to dashboard subscribers.
type Event struct {
	// ID is the monotonic sequence identifier assigned by the broadcaster.
	ID uint64 `json:"-"`
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

// Broadcaster maintains org-scoped subscriber lists and an in-memory event ring
// buffer for SSE delivery and replay.
type Broadcaster struct {
	bufferSize   int
	ringCapacity int
	mu           sync.RWMutex
	subs         map[string]map[uint64]chan Event
	nextID       atomic.Uint64
	nextEventID  uint64
	ring         []Event
	metrics      *metrics.Registry
}

// Metric names published by the broadcaster.
const (
	sseSubscribersMetric   = "undolog_proxy_sse_subscribers"
	sseEventsDroppedMetric = "undolog_proxy_sse_events_dropped_total"
)

const defaultRingCapacity = 1000

// SetMetrics wires the registry so subscriber counts and dropped events are
// exposed on /metrics. A nil registry disables instrumentation.
func (b *Broadcaster) SetMetrics(reg *metrics.Registry) {
	b.metrics = reg
}

// NewBroadcaster creates a broadcaster with the given subscriber channel buffer size.
func NewBroadcaster(bufferSize int) *Broadcaster {
	if bufferSize <= 0 {
		bufferSize = 128
	}
	return &Broadcaster{
		bufferSize:   bufferSize,
		ringCapacity: defaultRingCapacity,
		subs:         make(map[string]map[uint64]chan Event),
		ring:         make([]Event, 0, defaultRingCapacity),
	}
}

// Emit assigns a monotonic integer event ID, appends the event to the in-memory
// ring buffer, and sends the event to every subscriber registered for the matching org.
func (b *Broadcaster) Emit(evt Event) {
	if evt.OrgID == "" {
		return
	}
	evt.Timestamp = evt.Timestamp.UTC()
	if evt.Timestamp.IsZero() {
		evt.Timestamp = time.Now().UTC()
	}

	b.mu.Lock()
	defer b.mu.Unlock()

	b.nextEventID++
	evt.ID = b.nextEventID

	if len(b.ring) < b.ringCapacity {
		b.ring = append(b.ring, evt)
	} else {
		copy(b.ring, b.ring[1:])
		b.ring[len(b.ring)-1] = evt
	}

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

// Subscribe registers one org-scoped SSE subscriber and returns any replayable
// events for the requested lastEventID, the live event channel, and an unsubscribe function.
func (b *Broadcaster) Subscribe(orgID string, lastEventID *uint64) ([]Event, <-chan Event, func()) {
	ch := make(chan Event, b.bufferSize)
	id := b.nextID.Add(1)

	b.mu.Lock()
	if b.subs[orgID] == nil {
		b.subs[orgID] = make(map[uint64]chan Event)
	}
	b.subs[orgID][id] = ch
	b.recordSubscriberMetric(orgID, len(b.subs[orgID]))

	var replay []Event
	if lastEventID != nil {
		reqID := *lastEventID
		if len(b.ring) > 0 && reqID < b.ring[0].ID {
			replay = []Event{{
				Type:      EventResyncRequired,
				Timestamp: time.Now().UTC(),
				OrgID:     orgID,
			}}
		} else if reqID <= b.nextEventID {
			for i := range b.ring {
				evt := &b.ring[i]
				if evt.ID > reqID && evt.OrgID == orgID {
					replay = append(replay, *evt)
				}
			}
		}
	}
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

	return replay, ch, unsubscribe
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
