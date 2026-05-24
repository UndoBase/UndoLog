// Package sse tests the dashboard event broadcaster and SSE handler.
//
// These tests verify org-scoped fan-out and the response shape of the SSE
// endpoint used by the dashboard.
package sse

import (
	"context"
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
	req := httptest.NewRequest(http.MethodGet, "/events?org_id=org-1", nil).WithContext(ctx)
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
