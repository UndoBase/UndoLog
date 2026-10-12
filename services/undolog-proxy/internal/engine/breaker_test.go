package engine

import (
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// connFailure is a transport-level failure, the only kind that counts
// toward the breaker.
var connFailure = status.Error(codes.Unavailable, "engine down")

// TestGateCapsInFlight verifies the gate admits exactly max callers and
// frees a slot on release, which is the structural pool bound.
func TestGateCapsInFlight(t *testing.T) {
	g := newGate(2)
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}
	if !g.tryAcquire() {
		t.Fatal("gate must admit a caller below the cap")
	}
	if g.tryAcquire() {
		t.Fatal("gate must refuse a caller at the cap")
	}
	if got := g.inFlightCount(); got != 2 {
		t.Fatalf("in flight = %d, want 2", got)
	}
	g.release()
	if !g.tryAcquire() {
		t.Fatal("release must free a slot")
	}
	g.release()
	g.release()
	if got := g.inFlightCount(); got != 0 {
		t.Fatalf("in flight after all releases = %d, want 0", got)
	}
}

// TestGateReleaseWakesEveryWaiter verifies each release closes the signal
// current at that moment, so queued callers never sleep through a freed
// slot and never wait for a release that already happened.
func TestGateReleaseWakesEveryWaiter(t *testing.T) {
	g := newGate(1)
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}
	signal := g.waitForRelease()
	g.release()
	select {
	case <-signal:
	default:
		t.Fatal("release must close the signal current at release time")
	}
	if !g.tryAcquire() {
		t.Fatal("the freed slot must be acquirable")
	}
	g.release()
}

// TestBreakerOpensAfterConsecutiveFailures verifies the breaker stays closed
// below the threshold run of transport failures and opens at it.
func TestBreakerOpensAfterConsecutiveFailures(t *testing.T) {
	b := newBreaker(3, 30*time.Second)
	b.record(connFailure)
	b.record(connFailure)
	if !b.allow() {
		t.Fatal("failures below the threshold must leave the breaker closed")
	}
	b.record(connFailure)
	if b.allow() {
		t.Fatal("the threshold run of failures must open the breaker")
	}
	if got := b.currentState(); got != breakerOpen {
		t.Fatalf("breaker state = %d, want open (%d)", got, breakerOpen)
	}
}

// TestBreakerIgnoresEngineAnswers verifies only connection loss counts: an
// error the engine answered, or no error at all, resets the failure run so
// a healthy engine is never marked down by deterministic rejections.
func TestBreakerIgnoresEngineAnswers(t *testing.T) {
	b := newBreaker(2, 30*time.Second)
	b.record(connFailure)
	b.record(status.Error(codes.InvalidArgument, "bad request"))
	if !b.allow() {
		t.Fatal("an error the engine answered must reset the failure run")
	}
	b.record(nil)
	b.record(connFailure)
	if !b.allow() {
		t.Fatal("one failure below the threshold must leave the breaker closed")
	}
	b.record(connFailure)
	if b.allow() {
		t.Fatal("two consecutive transport failures must open the breaker")
	}
}

// TestBreakerHalfOpenClosesOnFirstSuccess verifies the breaker probes alone
// after the reset interval and closes on the first healthy answer.
func TestBreakerHalfOpenClosesOnFirstSuccess(t *testing.T) {
	b := newBreaker(1, 30*time.Second)
	fake := time.Now()
	b.now = func() time.Time { return fake }
	var mu sync.Mutex
	var seen []breakerState
	b.onChanged = func(s breakerState) {
		mu.Lock()
		seen = append(seen, s)
		mu.Unlock()
	}

	b.record(connFailure)
	if b.allow() {
		t.Fatal("an open breaker must reject before the reset interval")
	}
	fake = fake.Add(31 * time.Second)
	if !b.allow() {
		t.Fatal("half-open must admit a probe after the reset interval")
	}
	if b.allow() {
		t.Fatal("half-open must admit a single probe at a time")
	}
	b.record(nil)
	if got := b.currentState(); got != breakerClosed {
		t.Fatalf("breaker state after a healthy probe = %d, want closed (%d)", got, breakerClosed)
	}
	if !b.allow() {
		t.Fatal("a closed breaker must admit calls again")
	}
	waitUntil(t, func() bool {
		mu.Lock()
		defer mu.Unlock()
		return len(seen) == 3
	})
	mu.Lock()
	defer mu.Unlock()
	want := []breakerState{breakerOpen, breakerHalfOpen, breakerClosed}
	for i, state := range want {
		if seen[i] != state {
			t.Fatalf("published state %d = %d, want %d", i, seen[i], state)
		}
	}
}

// TestBreakerHalfOpenReopensOnFailure verifies a failed probe reopens the
// breaker with a fresh reset interval instead of letting calls through.
func TestBreakerHalfOpenReopensOnFailure(t *testing.T) {
	b := newBreaker(1, 30*time.Second)
	fake := time.Now()
	b.now = func() time.Time { return fake }

	b.record(connFailure)
	fake = fake.Add(31 * time.Second)
	if !b.allow() {
		t.Fatal("half-open must admit a probe after the reset interval")
	}
	b.record(connFailure)
	if b.allow() {
		t.Fatal("a failed probe must reopen the breaker")
	}
	fake = fake.Add(31 * time.Second)
	if !b.allow() {
		t.Fatal("the reopened breaker must probe again after the interval")
	}
}

// TestBreakerReleaseProbeReturnsUnusedPermit verifies a probe that is
// rejected before reaching the engine gives its permit back, so the next
// caller may probe instead of waiting out the reset interval.
func TestBreakerReleaseProbeReturnsUnusedPermit(t *testing.T) {
	b := newBreaker(1, 30*time.Second)
	fake := time.Now()
	b.now = func() time.Time { return fake }
	b.record(connFailure)
	fake = fake.Add(31 * time.Second)
	if !b.allow() {
		t.Fatal("half-open must admit a probe after the reset interval")
	}
	b.releaseProbe()
	if !b.allow() {
		t.Fatal("an unused probe permit must be returned to the next caller")
	}
}

// TestBreakerReleaseProbeIsNoOpWhenClosed verifies the permit return never
// disturbs a closed breaker.
func TestBreakerReleaseProbeIsNoOpWhenClosed(t *testing.T) {
	b := newBreaker(1, 30*time.Second)
	b.releaseProbe()
	if !b.allow() {
		t.Fatal("releaseProbe must be a no-op outside half-open")
	}
}
