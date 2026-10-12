package engine

import (
	"sync"
	"time"
)

// breakerState is the value published on the breaker state gauge.
type breakerState int

const (
	breakerClosed   breakerState = 0
	breakerHalfOpen breakerState = 1
	breakerOpen     breakerState = 2
)

// gate bounds how many engine RPCs run at once. The cap is the structural
// reason the engine's database pool cannot exhaust: fewer requests can be
// waiting on the engine than the pool can serve. max is always positive:
// the client builds a gate only when a cap is configured.
type gate struct {
	mu        sync.Mutex
	max       int
	inFlight  int
	freed     chan struct{}
	onChanged func(int)
}

func newGate(limit int) *gate {
	return &gate{max: limit, freed: make(chan struct{})}
}

// tryAcquire takes a slot when one is free and reports whether it did.
func (g *gate) tryAcquire() bool {
	g.mu.Lock()
	if g.inFlight >= g.max {
		g.mu.Unlock()
		return false
	}
	g.inFlight++
	g.mu.Unlock()
	g.notify()
	return true
}

// release frees one slot and wakes every caller waiting for the next one.
// Each release closes the channel current at that moment and installs a
// fresh one, so no channel is closed twice and every waiter wakes.
func (g *gate) release() {
	g.mu.Lock()
	g.inFlight--
	close(g.freed)
	g.freed = make(chan struct{})
	g.mu.Unlock()
	g.notify()
}

// waitForRelease returns the channel that closes when the next slot frees.
func (g *gate) waitForRelease() <-chan struct{} {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.freed
}

// inFlightCount reports how many slots are currently taken.
func (g *gate) inFlightCount() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.inFlight
}

// notify publishes the current in-flight count to the in-flight gauge.
func (g *gate) notify() {
	if g.onChanged == nil {
		return
	}
	g.onChanged(g.inFlightCount())
}

// breaker stops calls to an engine that keeps failing at the transport
// level and probes it again after a reset interval. Only connection loss
// counts as a failure: an error the engine answered means it is reachable,
// so a deterministic rejection or an authentication mistake never opens
// the breaker. Lazy transitions replace a timer: the state advances when
// the next call is admitted, which needs no background goroutine.
type breaker struct {
	mu         sync.Mutex
	threshold  int
	resetAfter time.Duration
	failures   int
	state      breakerState
	openedAt   time.Time
	probing    bool
	now        func() time.Time
	onChanged  func(breakerState)
}

func newBreaker(threshold int, resetAfter time.Duration) *breaker {
	return &breaker{threshold: threshold, resetAfter: resetAfter, now: time.Now}
}

// allow reports whether a call may reach the engine. In half-open state it
// admits a single probe at a time so a recovering engine is not stampeded.
func (b *breaker) allow() bool {
	b.mu.Lock()
	allowed := false
	transitioned := false
	switch b.state {
	case breakerClosed:
		allowed = true
	case breakerHalfOpen:
		if !b.probing {
			b.probing = true
			allowed = true
		}
	case breakerOpen:
		if b.now().Sub(b.openedAt) >= b.resetAfter {
			b.state = breakerHalfOpen
			b.probing = true
			allowed = true
			transitioned = true
		}
	}
	b.mu.Unlock()
	if transitioned {
		b.notify()
	}
	return allowed
}

// record notes the outcome of a call that was allowed through. A healthy
// answer (no error, or an error the engine produced) resets the failure
// run and closes a half-open breaker; a connection failure reopens it.
func (b *breaker) record(err error) {
	healthy := err == nil || !isConnectionLoss(err)
	b.mu.Lock()
	changed := false
	switch b.state {
	case breakerClosed:
		if healthy {
			b.failures = 0
		} else {
			b.failures++
			if b.failures >= b.threshold {
				b.state = breakerOpen
				b.openedAt = b.now()
				changed = true
			}
		}
	case breakerHalfOpen:
		b.probing = false
		if healthy {
			b.state = breakerClosed
			b.failures = 0
		} else {
			b.state = breakerOpen
			b.openedAt = b.now()
		}
		changed = true
	case breakerOpen:
		// A call admitted before the open cannot change the state.
	}
	b.mu.Unlock()
	if changed {
		b.notify()
	}
}

// releaseProbe returns an unused half-open probe permit when the request is
// rejected before reaching the engine, so the next caller may probe instead
// of waiting out the reset interval. It is a no-op in every other state.
func (b *breaker) releaseProbe() {
	b.mu.Lock()
	if b.state == breakerHalfOpen && b.probing {
		b.probing = false
	}
	b.mu.Unlock()
}

// currentState reports the state for publication on the state gauge.
func (b *breaker) currentState() breakerState {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.state
}

// notify publishes the current state to the breaker state gauge.
func (b *breaker) notify() {
	if b.onChanged == nil {
		return
	}
	b.onChanged(b.currentState())
}
