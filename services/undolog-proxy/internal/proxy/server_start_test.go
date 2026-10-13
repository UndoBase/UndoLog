// Package proxy tests the work Start performs during the startup pass under
// concurrency.
//
// Start reconciles approvals, then the broadcaster, metrics, and approval
// store all share state with the request path once serving begins. This file
// races the startup operations against one another and against the publish
// and subscribe paths to confirm each structure is guarded, so the race
// detector staying clean is the assertion.
package proxy

import (
	"context"
	"strings"
	"sync"
	"testing"
	"time"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
	"undolog-proxy/internal/sse"
)

// TestStartupPassRacesSafely runs the shared-state operations the startup
// pass performs from many goroutines at once: the approval reconcile that
// rebuilds the approval store, the first metric registrations, and the
// broadcaster publish and subscribe the request path drives. Each goroutine
// also reads the registry so registration is exercised against a concurrent
// reader. The race detector is the assertion that the structures are
// guarded, and the final checks confirm reconcile populated the store and
// every subscriber left the gauge back at zero.
func TestStartupPassRacesSafely(t *testing.T) {
	registry := metrics.NewRegistry()
	engine := &mockEngineClient{
		pending: []protocol.ApprovalRecord{
			{ApprovalID: "ap-1", OrgID: "org-1", SessionID: "s-1", EffectID: "e-1", ToolName: "delete_user", CreatedAtUnix: 1700000000000},
		},
	}
	server, err := NewServer(Config{
		ListenAddr:            ":0",
		RequestTimeout:        time.Second,
		DashboardEventBufSize: 8,
		EngineGRPCAddr:        "engine:50051",
		UpstreamToolURL:       "http://upstream",
		TrustedAPIKeys:        map[string]string{"key-1": "org-1"},
	}, engine, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), registry, nil)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}

	const workers = 16
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			server.reconcileOnce(context.Background())
			events, unsubscribe := server.broadcaster.Subscribe("org-1")
			server.broadcaster.Emit(sse.Event{Type: sse.EventApprovalRequired, OrgID: "org-1", ApprovalID: "ap-1"})
			select {
			case evt := <-events:
				if evt.ApprovalID != "ap-1" {
					t.Errorf("delivered event has approval %q, want ap-1", evt.ApprovalID)
				}
			case <-time.After(time.Second):
				t.Error("subscriber never received the published event")
			}
			unsubscribe()
			_ = registry.Render()
		}()
	}
	close(start)
	wg.Wait()

	if _, ok := server.approvals.Get("ap-1"); !ok {
		t.Fatal("concurrent startup reconcile did not leave approval ap-1 in the store")
	}
	rendered := registry.Render()
	if gauge := `undolog_proxy_sse_subscribers{org="org-1"} 0`; !strings.Contains(rendered, gauge) {
		t.Fatalf("subscribers gauge must settle at zero once every subscriber unsubscribes:\n%s", rendered)
	}
}
