package engine

import (
	"context"
	"errors"
	"sync"
	"time"
)

// Errors returned by queue admission. The client maps each one to a 503
// answer wrapped around protocol.ErrEngineBusy and labels the rejection
// metric with the queue reason.
var (
	errQueueFull   = errors.New("engine queue is full")
	errWaitExpired = errors.New("engine queue wait budget exhausted")
)

// queue bounds how many engine RPCs may wait for a gate slot. A bounded
// queue absorbs micro-bursts that would otherwise be rejected the moment
// the gate is full, while still failing fast when the outage outlasts the
// wait budget. Depth zero is never used: the client builds a queue only
// when both a depth and a budget are configured.
type queue struct {
	mu        sync.Mutex
	max       int
	budget    time.Duration
	waiting   int
	onChanged func(int)
}

func newQueue(limit int, budget time.Duration) *queue {
	return &queue{max: limit, budget: budget}
}

// admit takes a gate slot for the caller. It takes one immediately when a
// slot is free, and otherwise waits up to the budget for the next release,
// bounded by the queue depth. It returns nil when the gate admitted the
// caller, errQueueFull when no wait slot is free, errWaitExpired when the
// budget elapses first, or ctx.Err when the caller's own context ends while
// waiting: every exit without a slot leaves the engine untouched.
func (q *queue) admit(ctx context.Context, g *gate) error {
	if g.tryAcquire() {
		return nil
	}
	q.mu.Lock()
	if q.waiting >= q.max {
		q.mu.Unlock()
		return errQueueFull
	}
	q.waiting++
	q.mu.Unlock()
	q.notify()
	defer func() {
		q.mu.Lock()
		q.waiting--
		q.mu.Unlock()
		q.notify()
	}()

	timer := time.NewTimer(q.budget)
	defer timer.Stop()
	for {
		// Fetch the release signal before trying the gate: a release that
		// happens in between closes this channel, so the loop can never
		// sleep through a freed slot.
		justFreed := g.waitForRelease()
		if g.tryAcquire() {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			return errWaitExpired
		case <-justFreed:
		}
	}
}

// depth reports how many callers are currently waiting for a slot.
func (q *queue) depth() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.waiting
}

// notify publishes the current depth to the queue depth gauge.
func (q *queue) notify() {
	if q.onChanged == nil {
		return
	}
	q.onChanged(q.depth())
}
