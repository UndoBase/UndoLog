// Package engine tests the reconnect behavior of the gRPC client against a
// real server.
//
// The restart test pins the lazy-connection contract: the channel is created on
// the first RPC, and after the engine goes away and comes back the same client
// recovers without the caller issuing a new dial. It exercises the production
// dial path, so it doubles as a smoke check for the wire protocol.
package engine

import (
	"context"
	"errors"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/grpc"

	"undolog-proxy/internal/engine/pb"
	"undolog-proxy/internal/protocol"
)

// stubEngine is the smallest server that satisfies the generated service
// interface and returns a deterministic intercept outcome for the test.
type stubEngine struct {
	pb.UnimplementedUndoLogEngineServer
	intercepts atomic.Int32
}

// Intercept returns an execute decision so the client observes a successful,
// routable response rather than a replay or an approval hold.
func (s *stubEngine) Intercept(ctx context.Context, _ *pb.InterceptRequest) (*pb.InterceptResponse, error) {
	s.intercepts.Add(1)
	return &pb.InterceptResponse{
		Outcome: &pb.InterceptResponse_Execute{
			Execute: &pb.ExecuteOutcome{EffectId: "effect-1"},
		},
	}, nil
}

// startEngine runs a stub engine on the given address ("127.0.0.1:0" picks a
// free port). Rebinding the same port right after a server stopped can briefly
// collide with TIME_WAIT, so the listen is retried for a short window. It
// returns the server, the stub, and the actual bound address.
func startEngine(t *testing.T, address string) (*grpc.Server, *stubEngine, string) {
	t.Helper()
	var (
		lis net.Listener
		err error
	)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		lis, err = net.Listen("tcp", address)
		if err == nil {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	stub := &stubEngine{}
	srv := grpc.NewServer()
	pb.RegisterUndoLogEngineServer(srv, stub)
	go func() { _ = srv.Serve(lis) }()
	return srv, stub, lis.Addr().String()
}

// eventually polls the predicate until it returns nil or the deadline passes.
func eventually(t *testing.T, timeout time.Duration, what string, pred func() error) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	var last error
	for time.Now().Before(deadline) {
		if last = pred(); last == nil {
			return
		}
		time.Sleep(25 * time.Millisecond)
	}
	t.Fatalf("%s: timed out: %v", what, last)
}

// interceptRequest builds the minimal request the stub engine understands.
func interceptRequest() protocol.InterceptRequest {
	return protocol.InterceptRequest{
		ToolCall: protocol.ToolCall{ToolName: "stub_tool"},
	}
}

// TestClientReconnectsAfterEngineRestart is the end-to-end guarantee of the
// lazy connection: the same client serves an RPC before the outage, fails while
// the engine is down, and serves again after it comes back on the same address.
func TestClientReconnectsAfterEngineRestart(t *testing.T) {
	srv, _, addr := startEngine(t, "127.0.0.1:0")
	defer func() { srv.Stop() }()

	client := NewClient(addr, RetryConfig{MaxAttempts: 3, Backoff: 5 * time.Millisecond}, nil)
	defer func() { _ = client.Close() }()

	// Each probe gets its own short deadline so a down engine fails fast, and
	// the context is cancelled as soon as the call returns to avoid piling up
	// timers across the poll loops.
	withDeadline := func() (context.Context, context.CancelFunc) {
		return context.WithTimeout(context.Background(), 2*time.Second)
	}

	// Engine is up: the first RPC lazy-creates the channel and routes.
	executed := func() error {
		ctx, cancel := withDeadline()
		defer cancel()
		resp, err := client.Intercept(ctx, interceptRequest())
		if err != nil {
			return err
		}
		if resp.EffectID != "effect-1" {
			return errors.New("unexpected effect id")
		}
		return nil
	}
	if err := executed(); err != nil {
		t.Fatalf("intercept against the running engine: %v", err)
	}

	// Engine goes away: the channel sees the connection loss and RPCs fail.
	srv.Stop()
	eventually(t, 5*time.Second, "engine outage surfaced", func() error {
		ctx, cancel := withDeadline()
		defer cancel()
		_, err := client.Intercept(ctx, interceptRequest())
		if err == nil {
			return errors.New("expected the RPC to fail while the engine is down")
		}
		return nil
	})

	// Engine comes back on the same address: the channel reconnects on demand.
	restarted, restubbed, _ := startEngine(t, addr)
	defer func() { restarted.Stop() }()
	eventually(t, 15*time.Second, "engine recovery", executed)
	if restubbed.intercepts.Load() == 0 {
		t.Fatal("the recovered client must route to the restarted engine")
	}
}
