// Package engine tests the client behavior when many goroutines issue the
// first RPCs at once.
//
// The client creates its channel lazily and holds the mutex across that
// dial, so a burst of first use must produce exactly one channel when the
// dial succeeds and no cached transport when it fails. This file pins both
// halves of that contract with an injected counting dial function, the same
// injection point the single-caller tests use.
package engine

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/grpc"
)

// TestClientDialsOnceUnderConcurrentFirstRPCs issues the first RPCs from many
// goroutines at once and asserts the injected dial function ran exactly
// once: the mutex held across the dial in getTransport means every caller
// after the first reuses the shared channel rather than opening its own.
// The dialer sleeps briefly while holding the lock so the burst genuinely
// piles up behind it, and the counter would exceed one if the guard were
// ever removed.
func TestClientDialsOnceUnderConcurrentFirstRPCs(t *testing.T) {
	srv, stub, addr := startEngine(t, "127.0.0.1:0")
	defer func() { srv.Stop() }()

	client := NewClient(addr, RetryConfig{MaxAttempts: 1}, nil)
	defer func() { _ = client.Close() }()

	var dials atomic.Int32
	productionDial := client.dial
	client.dial = func(ctx context.Context, address string) (*grpc.ClientConn, error) {
		dials.Add(1)
		// getTransport holds the client mutex across this dial, so the pause
		// gives the other goroutines time to block on that mutex instead of
		// arriving after the channel already exists. The successful dial runs
		// once, so the pause delays the burst by a single dial.
		time.Sleep(20 * time.Millisecond)
		return productionDial(ctx, address)
	}

	const callers = 16
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			<-start
			resp, err := client.Intercept(ctx, interceptRequest())
			if err != nil {
				t.Errorf("concurrent first RPC failed: %v", err)
				return
			}
			if resp.EffectID != "effect-1" {
				t.Errorf("concurrent first RPC served effect %q, want effect-1", resp.EffectID)
			}
		}()
	}
	close(start)
	wg.Wait()

	if got := dials.Load(); got != 1 {
		t.Fatalf("dial count = %d, want exactly one shared channel for the first burst", got)
	}
	if got := stub.intercepts.Load(); got != callers {
		t.Fatalf("engine served %d intercepts, want one per caller (%d)", got, callers)
	}
}

// TestClientDoesNotCacheFailedDialUnderConcurrentFirstRPCs is the failure
// twin of the shared-channel test: when the first burst dials an engine that
// cannot be reached, the failure is never cached as a transport. Every
// caller waiting on the mutex pays its own dial attempt and surfaces the
// dial error, and the injected counter reading one per caller proves no
// caller received a half-built channel.
func TestClientDoesNotCacheFailedDialUnderConcurrentFirstRPCs(t *testing.T) {
	client := NewClient("127.0.0.1:1", RetryConfig{MaxAttempts: 1}, nil)
	defer func() { _ = client.Close() }()

	var dials atomic.Int32
	client.dial = func(_ context.Context, _ string) (*grpc.ClientConn, error) {
		dials.Add(1)
		return nil, errors.New("dial failed")
	}

	const callers = 8
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			<-start
			if _, err := client.Intercept(ctx, interceptRequest()); err == nil {
				t.Error("first RPC with a failing dial must surface the dial error")
			}
		}()
	}
	close(start)
	wg.Wait()

	if got := dials.Load(); got != callers {
		t.Fatalf("dial count = %d, want one attempt per caller: a failed dial is not cached", got)
	}
}
