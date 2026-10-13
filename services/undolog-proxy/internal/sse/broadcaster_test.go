// Package sse tests the dashboard event broadcaster and SSE handler.
//
// These tests verify org-scoped fan-out and the response shape of the SSE
// endpoint used by the dashboard.
package sse

import (
	"context"
	"encoding/json"
	"errors"
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
	ch, cancel := b.Subscribe("org-1")
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

	_, unsubscribe1 := b.Subscribe("org-1")
	_, _ = b.Subscribe("org-2")

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
	} else if unixNano, err := strconv.ParseInt(id, 10, 64); err != nil || unixNano <= 0 {
		t.Errorf("id line %q must carry the event time as Unix nanoseconds", lines[1])
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
