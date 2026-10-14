// Package engine tests readiness: the connection view behind the /readyz
// probe, the recovery metrics, and the watcher lifecycle, against real
// servers so the channel moves through the states production sees.
package engine

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/connectivity"

	"undolog-proxy/internal/metrics"
)

// TestClientNotReadyBeforeFirstConnect pins the probe before any successful
// connection: a client whose channel never reaches an engine reports not
// ready, and stays not ready once the channel has attempted and failed.
func TestClientNotReadyBeforeFirstConnect(t *testing.T) {
	client := NewClient("127.0.0.1:1", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, nil)
	defer func() { _ = client.Close() }()

	if client.Ready() {
		t.Fatal("a client whose channel never reached an engine must report not ready")
	}

	// Wait until the channel has actually attempted the connection and landed
	// in a failure state, then the probe must still be closed.
	eventually(t, 3*time.Second, "channel settles after a failed attempt", func() error {
		client.mu.Lock()
		conn := client.conn
		client.mu.Unlock()
		if conn == nil {
			return errors.New("channel not opened yet")
		}
		if conn.GetState() != connectivity.TransientFailure {
			return errors.New("channel still connecting")
		}
		return nil
	})
	if client.Ready() {
		t.Fatal("a failed channel must report not ready")
	}
}

// TestClientReadinessFollowsEngineRecovery walks the probe through a full
// engine lifecycle: ready without any RPC once the channel connects, not
// ready while the engine is down, then ready again with the recovery counter
// at one and the connected gauge back at one.
func TestClientReadinessFollowsEngineRecovery(t *testing.T) {
	srv, _, addr := startEngine(t, "127.0.0.1:0")
	defer func() { srv.Stop() }()

	reg := metrics.NewRegistry()
	client := NewClient(addr, RetryConfig{MaxAttempts: 3, Backoff: 5 * time.Millisecond}, nil)
	client.SetMetrics(reg)
	defer func() { _ = client.Close() }()

	// The probe opens the channel and the channel connects on its own, so
	// readiness converges without a tool call.
	eventually(t, 5*time.Second, "initial readiness", func() error {
		if client.Ready() {
			return nil
		}
		return errors.New("channel not ready yet")
	})
	eventually(t, 5*time.Second, "connected gauge", func() error {
		if strings.Contains(reg.Render(), "undolog_proxy_engine_connected 1\n") {
			return nil
		}
		return errors.New("connected gauge not at 1 yet")
	})
	if strings.Contains(reg.Render(), "undolog_proxy_engine_reconnects_total") {
		t.Fatalf("no recovery may be counted before any disconnect, got:\n%s", reg.Render())
	}

	// The engine goes away: the probe closes and the gauge falls.
	srv.Stop()
	eventually(t, 5*time.Second, "outage surfaced", func() error {
		if client.Ready() {
			return errors.New("still ready while the engine is down")
		}
		return nil
	})
	eventually(t, 5*time.Second, "connected gauge falls", func() error {
		if strings.Contains(reg.Render(), "undolog_proxy_engine_connected 0\n") {
			return nil
		}
		return errors.New("connected gauge not at 0 yet")
	})

	// The engine returns on the same address: readiness, the gauge, and the
	// recovery counter all report the reconnect.
	restarted, _, _ := startEngine(t, addr)
	defer func() { restarted.Stop() }()
	eventually(t, 15*time.Second, "readiness recovery", func() error {
		if client.Ready() {
			return nil
		}
		return errors.New("channel not ready after the restart")
	})
	eventually(t, 15*time.Second, "recovery counted", func() error {
		if strings.Contains(reg.Render(), "undolog_proxy_engine_reconnects_total 1\n") {
			return nil
		}
		return errors.New("recovery counter not at 1 yet")
	})
	if !strings.Contains(reg.Render(), "undolog_proxy_engine_connected 1\n") {
		t.Fatalf("connected gauge must return to 1 after recovery, got:\n%s", reg.Render())
	}
}

// TestEngineOutageKeepsRPCsFailingFast verifies readiness stays off the
// request path: with the engine down an Intercept fails promptly with the
// transport error instead of waiting for a connection that never comes.
func TestEngineOutageKeepsRPCsFailingFast(t *testing.T) {
	srv, _, addr := startEngine(t, "127.0.0.1:0")
	defer func() { srv.Stop() }()

	client := NewClient(addr, RetryConfig{MaxAttempts: 3, Backoff: 5 * time.Millisecond}, nil)
	defer func() { _ = client.Close() }()

	// Reach the engine through the probe first so the outage below lands on
	// an established channel, not on the initial dial.
	eventually(t, 5*time.Second, "initial readiness", func() error {
		if client.Ready() {
			return nil
		}
		return errors.New("channel not ready yet")
	})

	srv.Stop()
	eventually(t, 5*time.Second, "outage surfaced", func() error {
		if client.Ready() {
			return errors.New("still ready while the engine is down")
		}
		return nil
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	start := time.Now()
	_, err := client.Intercept(ctx, interceptRequest())
	elapsed := time.Since(start)
	if err == nil {
		t.Fatal("expected the RPC to fail while the engine is down")
	}
	if elapsed > 2*time.Second {
		t.Fatalf("the RPC waited %s for a failing engine; readiness must not stall the request path", elapsed)
	}
}

// TestClientWatcherStopsOnClose verifies no goroutine outlives the client:
// the watcher runs while the channel exists, exits when Close cancels it,
// and readiness plus a second Close stay well behaved afterwards.
func TestClientWatcherStopsOnClose(t *testing.T) {
	client := NewClient("127.0.0.1:1", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, nil)

	// The probe opens the channel, which is what starts the watcher.
	if client.Ready() {
		t.Fatal("a closed port must never report ready")
	}
	select {
	case <-client.watchDone:
		t.Fatal("the watcher must keep running while the channel exists")
	default:
	}

	if err := client.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	eventually(t, 3*time.Second, "watcher exit", func() error {
		select {
		case <-client.watchDone:
			return nil
		default:
			return errors.New("watcher still running")
		}
	})
	if client.Ready() {
		t.Fatal("a closed client must report not ready")
	}
	if err := client.Close(); err != nil {
		t.Fatalf("second close must be idempotent: %v", err)
	}
}

// TestClientReadyFalseForInjectedTransport pins the fail-closed edge: a
// client built with an injected transport does not observe its caller-owned
// channel, so readiness reports not ready instead of guessing a state.
func TestClientReadyFalseForInjectedTransport(t *testing.T) {
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, &mockTransport{}, nil)
	defer func() { _ = client.Close() }()

	if client.Ready() {
		t.Fatal("an injected transport must not report ready")
	}
}
