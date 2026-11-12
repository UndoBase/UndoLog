// Package sse tests the dashboard event broadcaster and SSE handler.
//
// These tests verify org-scoped fan-out, in-memory replay buffers, monotonic
// event IDs, and the response shape of the SSE endpoint used by the dashboard.
package sse

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"undolog-proxy/internal/metrics"
)

// TestBroadcasterEmitsToOrgSubscribers verifies org-scoped event delivery.
func TestBroadcasterEmitsToOrgSubscribers(t *testing.T) {
	b := NewBroadcaster(4)
	_, ch, cancel := b.Subscribe("org-1", nil)
	defer cancel()

	b.Emit(Event{Type: EventApprovalRequired, OrgID: "org-1", ApprovalID: "a-1"})
	select {
	case evt := <-ch:
		if evt.ApprovalID != "a-1" || evt.OrgID != "org-1" {
			t.Fatalf("unexpected event: %+v", evt)
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for event")
	}
}

// TestBroadcasterHandlerStreamsSSE verifies the SSE endpoint response contract.
func TestBroadcasterHandlerStreamsSSE(t *testing.T) {
	b := NewBroadcaster(4)
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(ctx)
	req.Header.Set("X-Org-Id", "org-1")
	rec := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		b.Handler(rec, req)
		close(done)
	}()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not exit")
	}
	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", rec.Code)
	}
	if got := rec.Header().Get("Content-Type"); got != "text/event-stream" {
		t.Fatalf("unexpected content type: %q", got)
	}
}

// TestBroadcasterHandlerRejectsMissingOrg verifies the handler requires an org identity.
func TestBroadcasterHandlerRejectsMissingOrg(t *testing.T) {
	b := NewBroadcaster(4)
	req := httptest.NewRequest(http.MethodGet, "/events", nil)
	rec := httptest.NewRecorder()

	b.Handler(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without org identity, got %d", rec.Code)
	}
}

// TestBroadcasterCloseEndsHandler verifies Close unblocks in-flight SSE handlers,
// which is what lets graceful shutdown complete without waiting on live streams.
func TestBroadcasterCloseEndsHandler(t *testing.T) {
	b := NewBroadcaster(4)
	req := httptest.NewRequest(http.MethodGet, "/events", nil)
	req.Header.Set("X-Org-Id", "org-1")
	rec := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		b.Handler(rec, req)
		close(done)
	}()

	// Give the handler time to subscribe before closing the broadcaster.
	time.Sleep(50 * time.Millisecond)
	b.Close()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not exit after broadcaster close")
	}
}

// TestBroadcasterHandlerIgnoresQueryOrg verifies the org_id query fallback is removed.
func TestBroadcasterHandlerIgnoresQueryOrg(t *testing.T) {
	b := NewBroadcaster(4)
	req := httptest.NewRequest(http.MethodGet, "/events?org_id=org-1", nil)
	rec := httptest.NewRecorder()

	b.Handler(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with only query org_id, got %d", rec.Code)
	}
}

// failWriter implements http.ResponseWriter but errors on every payload write,
// simulating a connection that died silently mid-stream.
type failWriter struct {
	headers http.Header
	code    int
}

func (w *failWriter) Header() http.Header { return w.headers }

func (w *failWriter) WriteHeader(code int) { w.code = code }

func (w *failWriter) Write([]byte) (int, error) { return 0, errors.New("write failed") }

func (w *failWriter) Flush() {}

// TestBroadcasterHandlerExitsOnWriteError verifies a failed stream write ends
// the handler instead of leaking the subscription goroutine.
func TestBroadcasterHandlerExitsOnWriteError(t *testing.T) {
	b := NewBroadcaster(4)
	req := httptest.NewRequest(http.MethodGet, "/events", nil)
	req.Header.Set("X-Org-Id", "org-1")
	w := &failWriter{headers: make(http.Header)}

	done := make(chan struct{})
	go func() {
		b.Handler(w, req)
		close(done)
	}()

	// Give the handler time to subscribe before emitting the failing frame.
	time.Sleep(50 * time.Millisecond)
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", SessionID: "s-1", EffectID: "e-1"})

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not exit after write error")
	}
}

// TestBroadcasterMetricsSubscribersAndDrops verifies the subscriber gauge and
// dropped-event counter are published per org to the shared registry.
func TestBroadcasterMetricsSubscribersAndDrops(t *testing.T) {
	reg := metrics.NewRegistry()
	b := NewBroadcaster(1)
	b.SetMetrics(reg)

	_, _, unsubscribe1 := b.Subscribe("org-1", nil)
	_, _, _ = b.Subscribe("org-2", nil)

	rendered := reg.Render()
	if !strings.Contains(rendered, `undolog_proxy_sse_subscribers{org="org-1"} 1`) {
		t.Errorf("expected one org-1 subscriber, got:\n%s", rendered)
	}
	if !strings.Contains(rendered, `undolog_proxy_sse_subscribers{org="org-2"} 1`) {
		t.Errorf("expected one org-2 subscriber, got:\n%s", rendered)
	}

	// A buffer of 1 drops the second event and should record the drop.
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", SessionID: "s-1"})
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", SessionID: "s-2"})
	rendered = reg.Render()
	if !strings.Contains(rendered, `undolog_proxy_sse_events_dropped_total{org="org-1"} 1`) {
		t.Errorf("expected one dropped event, got:\n%s", rendered)
	}

	unsubscribe1()
	rendered = reg.Render()
	if !strings.Contains(rendered, `undolog_proxy_sse_subscribers{org="org-1"} 0`) {
		t.Errorf("expected org-1 gauge back to zero, got:\n%s", rendered)
	}
}

// frameWriter records the streamed bytes and signals once the first write
// lands, so a test can assert the frame format without racing the handler.
type frameWriter struct {
	*httptest.ResponseRecorder
	sig  chan struct{}
	once sync.Once
}

// Write records the payload and releases the waiting test on the first frame.
func (w *frameWriter) Write(b []byte) (int, error) {
	n, err := w.ResponseRecorder.Write(b)
	w.once.Do(func() { close(w.sig) })
	return n, err
}

// TestBroadcasterHandlerWritesStandardSSEFrame verifies one delivered event
// uses the SSE wire format: an event, id, and data line followed by the
// blank-line terminator.
func TestBroadcasterHandlerWritesStandardSSEFrame(t *testing.T) {
	b := NewBroadcaster(4)
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(ctx)
	req.Header.Set("X-Org-Id", "org-1")
	w := &frameWriter{ResponseRecorder: httptest.NewRecorder(), sig: make(chan struct{})}

	done := make(chan struct{})
	go func() {
		b.Handler(w, req)
		close(done)
	}()

	// Emit until a frame lands: the handler subscribes after flushing the
	// response headers, so an earlier emit would be dropped.
	emitted := false
	for i := 0; i < 10 && !emitted; i++ {
		b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", SessionID: "s-1", EffectID: "e-1"})
		select {
		case <-w.sig:
			emitted = true
		case <-time.After(20 * time.Millisecond):
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not stop")
	}
	if !emitted {
		t.Fatal("no frame was written")
	}

	body := w.Body.String()
	frame, _, terminated := strings.Cut(body, "\n\n")
	if !terminated {
		t.Fatalf("frame must end with a blank line, got %q", body)
	}
	lines := strings.Split(frame, "\n")
	if len(lines) != 3 {
		t.Fatalf("frame must carry event, id, and data lines, got %q", frame)
	}
	if lines[0] != "event: effect_committed" {
		t.Errorf("event line = %q, want %q", lines[0], "event: effect_committed")
	}
	id := strings.TrimPrefix(lines[1], "id: ")
	if id == lines[1] {
		t.Errorf("frame is missing its id line: %q", lines[1])
	} else if seq, err := strconv.ParseUint(id, 10, 64); err != nil || seq <= 0 {
		t.Errorf("id line %q must carry a positive monotonic sequence integer", lines[1])
	}
	data, isJSON := strings.CutPrefix(lines[2], "data: ")
	if !isJSON {
		t.Fatalf("frame is missing its data line: %q", lines[2])
	}
	var evt Event
	if err := json.Unmarshal([]byte(data), &evt); err != nil {
		t.Fatalf("data line must be the event JSON: %v", err)
	}
	if evt.Type != EventEffectCommitted || evt.EffectID != "e-1" || evt.OrgID != "org-1" {
		t.Fatalf("data line carries %+v, want the emitted event", evt)
	}
}

// TestMonotonicIntegerIDsIncrement verifies that every event emitted receives
// a sequential positive integer identifier starting at 1.
func TestMonotonicIntegerIDsIncrement(t *testing.T) {
	b := NewBroadcaster(16)
	_, ch, cancel := b.Subscribe("org-1", nil)
	defer cancel()

	const count = 10
	for i := 0; i < count; i++ {
		b.Emit(Event{Type: EventApprovalRequired, OrgID: "org-1", ApprovalID: fmt.Sprintf("ap-%d", i)})
	}

	for i := 1; i <= count; i++ {
		select {
		case evt := <-ch:
			if evt.ID != uint64(i) {
				t.Fatalf("event ID = %d, want %d", evt.ID, i)
			}
		case <-time.After(time.Second):
			t.Fatalf("timed out waiting for event %d", i)
		}
	}
}

// TestOrganizationIsolationInReplay verifies that replayed events are strictly
// isolated to the subscriber's authenticated organization.
func TestOrganizationIsolationInReplay(t *testing.T) {
	b := NewBroadcaster(16)
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-1"})
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-2", EffectID: "e-2"})
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-3"})
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-2", EffectID: "e-4"})

	lastID := uint64(1)
	replay, _, cancel := b.Subscribe("org-1", &lastID)
	defer cancel()

	if len(replay) != 1 {
		t.Fatalf("expected 1 replayed event for org-1, got %d", len(replay))
	}
	if replay[0].EffectID != "e-3" || replay[0].OrgID != "org-1" {
		t.Fatalf("unexpected replayed event: %+v", replay[0])
	}
}

// TestReconnectWithValidLastEventID verifies that reconnecting with a valid
// Last-Event-ID replays all intermediate events before streaming live events.
func TestReconnectWithValidLastEventID(t *testing.T) {
	b := NewBroadcaster(16)
	for i := 1; i <= 5; i++ {
		b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: fmt.Sprintf("e-%d", i)})
	}

	lastID := uint64(2)
	replay, ch, cancel := b.Subscribe("org-1", &lastID)
	defer cancel()

	if len(replay) != 3 {
		t.Fatalf("expected 3 replayed events, got %d", len(replay))
	}
	for i, evt := range replay {
		expectedID := uint64(i + 3)
		if evt.ID != expectedID {
			t.Errorf("replay[%d].ID = %d, want %d", i, evt.ID, expectedID)
		}
		expectedEffect := fmt.Sprintf("e-%d", i+3)
		if evt.EffectID != expectedEffect {
			t.Errorf("replay[%d].EffectID = %q, want %q", i, evt.EffectID, expectedEffect)
		}
	}

	// Verify live events continue after replay.
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-6"})
	select {
	case evt := <-ch:
		if evt.ID != 6 || evt.EffectID != "e-6" {
			t.Fatalf("unexpected live event: %+v", evt)
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for live event after replay")
	}
}

// TestBoundedBufferTruncatesOldest verifies that the in-memory ring buffer
// maintains at most 1,000 events, dropping oldest events once capacity is exceeded.
func TestBoundedBufferTruncatesOldest(t *testing.T) {
	b := NewBroadcaster(16)
	const total = 1005
	for i := 1; i <= total; i++ {
		b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: fmt.Sprintf("e-%d", i)})
	}

	b.mu.RLock()
	ringLen := len(b.ring)
	oldestID := b.ring[0].ID
	newestID := b.ring[ringLen-1].ID
	b.mu.RUnlock()

	if ringLen != defaultRingCapacity {
		t.Fatalf("ring buffer length = %d, want %d", ringLen, defaultRingCapacity)
	}
	if oldestID != 6 {
		t.Fatalf("oldest event ID = %d, want 6", oldestID)
	}
	if newestID != total {
		t.Fatalf("newest event ID = %d, want %d", newestID, total)
	}
}

// TestExpiredLastEventIDEmitsResyncRequired verifies that a client presenting an
// expired Last-Event-ID receives a resync_required event before live events.
func TestExpiredLastEventIDEmitsResyncRequired(t *testing.T) {
	b := NewBroadcaster(16)
	const total = 1005
	for i := 1; i <= total; i++ {
		b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: fmt.Sprintf("e-%d", i)})
	}

	// Event 3 was truncated from the buffer (buffer holds 6..1005).
	lastID := uint64(3)
	replay, _, cancel := b.Subscribe("org-1", &lastID)
	defer cancel()

	if len(replay) != 1 {
		t.Fatalf("expected 1 event in replay, got %d", len(replay))
	}
	if replay[0].Type != EventResyncRequired {
		t.Fatalf("expected resync_required event, got %q", replay[0].Type)
	}
	if replay[0].OrgID != "org-1" {
		t.Fatalf("resync_required event has OrgID %q, want org-1", replay[0].OrgID)
	}
}

// TestMalformedLastEventIDReturns400 verifies that malformed Last-Event-ID headers
// fail immediately with HTTP 400 Bad Request.
func TestMalformedLastEventIDReturns400(t *testing.T) {
	b := NewBroadcaster(4)
	malformed := []string{"abc", "-1", "12.34", "99999999999999999999999999999999"}
	for _, val := range malformed {
		req := httptest.NewRequest(http.MethodGet, "/events", nil)
		req.Header.Set("X-Org-Id", "org-1")
		req.Header.Set("Last-Event-ID", val)
		rec := httptest.NewRecorder()

		b.Handler(rec, req)

		if rec.Code != http.StatusBadRequest {
			t.Errorf("Last-Event-ID %q returned status %d, want 400", val, rec.Code)
		}
	}
}

// TestLastEventIDNewerThanCurrentSequence verifies that a client presenting a
// Last-Event-ID ahead of the current sequence gets no replay and resumes live.
func TestLastEventIDNewerThanCurrentSequence(t *testing.T) {
	b := NewBroadcaster(4)
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-1"})

	futureID := uint64(100)
	replay, ch, cancel := b.Subscribe("org-1", &futureID)
	defer cancel()

	if len(replay) != 0 {
		t.Fatalf("expected 0 replay events for future sequence ID, got %d", len(replay))
	}

	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-2"})
	select {
	case evt := <-ch:
		if evt.EffectID != "e-2" {
			t.Fatalf("unexpected live event: %+v", evt)
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for live event")
	}
}

// TestConcurrentSubscribeAndEmit verifies that concurrent event emission and
// subscriptions do not produce data races, drop events unexpectedly, or panic.
func TestConcurrentSubscribeAndEmit(t *testing.T) {
	b := NewBroadcaster(64)
	const producers = 4
	const consumers = 4
	const eventsPerProducer = 50

	var wg sync.WaitGroup
	start := make(chan struct{})

	for p := 0; p < producers; p++ {
		wg.Add(1)
		go func(prodID int) {
			defer wg.Done()
			<-start
			for i := 0; i < eventsPerProducer; i++ {
				b.Emit(Event{
					Type:     EventEffectCommitted,
					OrgID:    "org-concurrent",
					EffectID: fmt.Sprintf("eff-%d-%d", prodID, i),
				})
			}
		}(p)
	}

	for c := 0; c < consumers; c++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			lastID := uint64(5)
			replay, ch, cancel := b.Subscribe("org-concurrent", &lastID)
			defer cancel()
			_ = len(replay)
			timeout := time.After(200 * time.Millisecond)
			for {
				select {
				case _, ok := <-ch:
					if !ok {
						return
					}
				case <-timeout:
					return
				}
			}
		}()
	}

	close(start)
	wg.Wait()
}

// TestHandlerReplaysAndStreamsOverHTTP verifies end-to-end HTTP replay and live
// streaming via the SSE handler.
func TestHandlerReplaysAndStreamsOverHTTP(t *testing.T) {
	b := NewBroadcaster(16)
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-1"})
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-2"})
	b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: "e-3"})

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(ctx)
	req.Header.Set("X-Org-Id", "org-1")
	req.Header.Set("Last-Event-ID", "1")
	rec := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		b.Handler(rec, req)
		close(done)
	}()

	// Give the handler time to write the replayed frames.
	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not exit")
	}

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d", rec.Code)
	}

	body := rec.Body.String()
	frames := strings.Split(strings.TrimSpace(body), "\n\n")
	if len(frames) != 2 {
		t.Fatalf("expected 2 replayed frames, got %d:\n%s", len(frames), body)
	}
	if !strings.Contains(frames[0], "id: 2") || !strings.Contains(frames[0], `"effect_id":"e-2"`) {
		t.Errorf("frame 0 does not contain event 2:\n%s", frames[0])
	}
	if !strings.Contains(frames[1], "id: 3") || !strings.Contains(frames[1], `"effect_id":"e-3"`) {
		t.Errorf("frame 1 does not contain event 3:\n%s", frames[1])
	}
}

// TestHandlerExpiredLastEventIDOverHTTP verifies HTTP emission of resync_required.
func TestHandlerExpiredLastEventIDOverHTTP(t *testing.T) {
	b := NewBroadcaster(16)
	for i := 1; i <= 1005; i++ {
		b.Emit(Event{Type: EventEffectCommitted, OrgID: "org-1", EffectID: fmt.Sprintf("e-%d", i)})
	}

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(ctx)
	req.Header.Set("X-Org-Id", "org-1")
	req.Header.Set("Last-Event-ID", "2")
	rec := httptest.NewRecorder()

	done := make(chan struct{})
	go func() {
		b.Handler(rec, req)
		close(done)
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("handler did not exit")
	}

	body := rec.Body.String()
	if !strings.Contains(body, "event: resync_required") {
		t.Fatalf("expected resync_required event in stream, got:\n%s", body)
	}
}
