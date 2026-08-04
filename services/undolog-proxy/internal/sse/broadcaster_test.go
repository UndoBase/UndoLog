// Package sse tests the dashboard event broadcaster and SSE handler.
//
// These tests verify org-scoped fan-out and the response shape of the SSE
// endpoint used by the dashboard.
package sse

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
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
