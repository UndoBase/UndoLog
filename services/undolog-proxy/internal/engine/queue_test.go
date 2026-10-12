package engine

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

// waitUntil polls cond until it holds, failing the test after a deadline so a
// blocked goroutine cannot hang the run.
func waitUntil(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition never became true")
		}
		time.Sleep(time.Millisecond)
	}
}

// TestQueueAdmitsWhenSlotFreesWithinBudget verifies a queued caller is
// admitted the moment the gate frees a slot, well inside the wait budget.
func TestQueueAdmitsWhenSlotFreesWithinBudget(t *testing.T) {
	g := newGate(1)
	q := newQueue(4, 2*time.Second)
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}

	admitted := make(chan error, 1)
	go func() {
		admitted <- q.admit(context.Background(), g)
	}()
	waitUntil(t, func() bool { return q.depth() == 1 })

	if g.tryAcquire() {
		t.Fatal("gate must stay full while a caller is queued")
	}
	g.release()

	if err := <-admitted; err != nil {
		t.Fatalf("queued caller must be admitted when the slot frees: %v", err)
	}
	if got := g.inFlightCount(); got != 1 {
		t.Fatalf("admitted caller must hold one slot, in flight = %d", got)
	}
	g.release()
}

// TestQueueRejectsWhenFull verifies a queue at its depth refuses the next
// caller immediately instead of stacking unbounded waiters.
func TestQueueRejectsWhenFull(t *testing.T) {
	g := newGate(1)
	q := newQueue(1, 2*time.Second)
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}

	waiter := make(chan error, 1)
	go func() {
		waiter <- q.admit(context.Background(), g)
	}()
	waitUntil(t, func() bool { return q.depth() == 1 })

	if err := q.admit(context.Background(), g); !errors.Is(err, errQueueFull) {
		t.Fatalf("admission over the depth = %v, want errQueueFull", err)
	}

	g.release()
	if err := <-waiter; err != nil {
		t.Fatalf("first waiter must still be admitted: %v", err)
	}
	g.release()
}

// TestQueueRejectsAfterWaitBudget verifies a caller that cannot get a slot
// inside the wait budget is rejected instead of waiting forever.
func TestQueueRejectsAfterWaitBudget(t *testing.T) {
	g := newGate(1)
	q := newQueue(2, 50*time.Millisecond)
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}
	defer g.release()

	err := q.admit(context.Background(), g)
	if !errors.Is(err, errWaitExpired) {
		t.Fatalf("admission after the budget = %v, want errWaitExpired", err)
	}
	if got := q.depth(); got != 0 {
		t.Fatalf("a rejected caller must leave the queue, depth = %d", got)
	}
}

// TestQueueWaitHonorsContext verifies a caller whose own context ends while
// queued stops waiting instead of holding a queue slot.
func TestQueueWaitHonorsContext(t *testing.T) {
	g := newGate(1)
	q := newQueue(2, 5*time.Second)
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}
	defer g.release()

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()

	err := q.admit(ctx, g)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("admission after the caller deadline = %v, want context deadline", err)
	}
	if got := q.depth(); got != 0 {
		t.Fatalf("a cancelled caller must leave the queue, depth = %d", got)
	}
}

// TestQueueDepthNotifiesWaiters verifies the depth gauge source reports the
// waiter count on every change, which is what the /metrics queue depth
// gauge renders.
func TestQueueDepthNotifiesWaiters(t *testing.T) {
	g := newGate(1)
	q := newQueue(4, 2*time.Second)
	var mu sync.Mutex
	var seen []int
	q.onChanged = func(n int) {
		mu.Lock()
		seen = append(seen, n)
		mu.Unlock()
	}
	seenCount := func() int {
		mu.Lock()
		defer mu.Unlock()
		return len(seen)
	}
	if !g.tryAcquire() {
		t.Fatal("gate must admit the first caller")
	}

	admitted := make(chan error, 1)
	go func() {
		admitted <- q.admit(context.Background(), g)
	}()
	waitUntil(t, func() bool { return seenCount() == 1 })
	mu.Lock()
	first := seen[0]
	mu.Unlock()
	if first != 1 {
		t.Fatalf("enqueue must publish depth 1, got %d", first)
	}

	g.release()
	if err := <-admitted; err != nil {
		t.Fatalf("queued caller must be admitted: %v", err)
	}
	waitUntil(t, func() bool { return seenCount() == 2 })
	mu.Lock()
	second := seen[1]
	mu.Unlock()
	if second != 0 {
		t.Fatalf("admission must publish depth 0, got %d", second)
	}
	g.release()
}
