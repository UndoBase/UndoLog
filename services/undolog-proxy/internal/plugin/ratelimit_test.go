// Package plugin tests the per-API-key rate limiter: burst accounting, lazy
// refill, per-key isolation, concurrent safety, the rejection counter on
// /metrics, and the digest identity contract with the authentication
// middleware.
package plugin

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"undolog-proxy/internal/metrics"
)

// meteredRequest returns a request carrying the digest identity the
// authentication middleware attaches, as the chain sees it.
func meteredRequest(digest string) *http.Request {
	r := httptest.NewRequest(http.MethodGet, "/approvals", nil)
	return r.WithContext(WithAPIKeyDigest(r.Context(), digest))
}

// TestRateLimiterAllowsBurstThenRejects verifies a fresh bucket serves the
// full burst and the next request is rejected with the whole-second wait.
func TestRateLimiterAllowsBurstThenRejects(t *testing.T) {
	limiter := NewRateLimiter(1, 2, metrics.NewRegistry()).(*rateLimiter)
	now := time.Now()

	for i := 1; i <= 2; i++ {
		wait, allowed := limiter.allow("digest-a", now)
		if !allowed {
			t.Fatalf("request %d within burst must pass, wait=%d", i, wait)
		}
	}
	wait, allowed := limiter.allow("digest-a", now)
	if allowed {
		t.Fatal("request over the burst must be rejected")
	}
	if wait != 1 {
		t.Fatalf("wait = %d seconds, want 1 at 1 rps", wait)
	}
}

// TestRateLimiterBeforeRejectsWithRetryAfter verifies the full hook contract:
// the first request passes, the exhausted one returns a 429 decision with a
// Retry-After header, and the rejection reaches the metrics registry.
func TestRateLimiterBeforeRejectsWithRetryAfter(t *testing.T) {
	registry := metrics.NewRegistry()
	limiter := NewRateLimiter(0.5, 1, registry)
	r := meteredRequest("digest-a")

	if d := limiter.Before(r); d.Reason != "" || d.Status != 0 {
		t.Fatalf("first request must pass, got %+v", d)
	}
	d := limiter.Before(r)
	if d.Status != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", d.Status)
	}
	if d.Reason == "" {
		t.Fatal("rejection must carry a reason")
	}
	if got := d.Headers["Retry-After"]; got != "2" {
		t.Fatalf("Retry-After = %q, want 2 seconds at 0.5 rps", got)
	}
	if body := registry.Render(); !strings.Contains(body, "undolog_proxy_rate_limit_rejections_total 1") {
		t.Fatalf("rejection counter missing from metrics:\n%s", body)
	}
}

// TestRateLimiterRefillsOverTime verifies lazy refill: an empty bucket earns
// tokens with elapsed time and blocks again after spending them.
func TestRateLimiterRefillsOverTime(t *testing.T) {
	limiter := NewRateLimiter(2, 1, metrics.NewRegistry()).(*rateLimiter)
	start := time.Now()

	if _, allowed := limiter.allow("digest-a", start); !allowed {
		t.Fatal("first request must pass")
	}
	if wait, allowed := limiter.allow("digest-a", start); allowed || wait != 1 {
		t.Fatalf("immediate retry: allowed=%v wait=%d, want rejected with wait 1", allowed, wait)
	}
	// Half a second at 2 rps earns exactly one token.
	if _, allowed := limiter.allow("digest-a", start.Add(500*time.Millisecond)); !allowed {
		t.Fatal("request after refill must pass")
	}
	if _, allowed := limiter.allow("digest-a", start.Add(500*time.Millisecond)); allowed {
		t.Fatal("request after spending the refilled token must be rejected")
	}
}

// TestRateLimiterMetersKeysIndependently verifies exhausting one key's
// bucket leaves every other key at its own full burst.
func TestRateLimiterMetersKeysIndependently(t *testing.T) {
	limiter := NewRateLimiter(1, 1, metrics.NewRegistry()).(*rateLimiter)
	now := time.Now()

	if _, allowed := limiter.allow("digest-a", now); !allowed {
		t.Fatal("digest-a first request must pass")
	}
	if _, allowed := limiter.allow("digest-a", now); allowed {
		t.Fatal("digest-a over the burst must be rejected")
	}
	if _, allowed := limiter.allow("digest-b", now); !allowed {
		t.Fatal("digest-b must have its own full bucket")
	}
}

// TestRateLimiterAllowsRequestsWithoutIdentity verifies the hook does not
// meter requests the authentication middleware never resolved: the chain
// mounts after auth, so those requests cannot be attributed to a key.
func TestRateLimiterAllowsRequestsWithoutIdentity(t *testing.T) {
	limiter := NewRateLimiter(1, 1, metrics.NewRegistry())
	r := httptest.NewRequest(http.MethodGet, "/approvals", nil)

	if d := limiter.Before(r); d.Reason != "" || d.Status != 0 {
		t.Fatalf("request without identity must pass, got %+v", d)
	}
}

// TestRateLimiterConcurrentAllowIsRaceFree verifies concurrent requests for
// one key are serialized by the bucket lock and admit exactly the burst.
// The suite runs under -race, so this doubles as the data race check.
func TestRateLimiterConcurrentAllowIsRaceFree(t *testing.T) {
	const burst = 8
	limiter := NewRateLimiter(0.001, burst, metrics.NewRegistry()).(*rateLimiter)
	now := time.Now()

	var allowed atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, ok := limiter.allow("digest-a", now); ok {
				allowed.Add(1)
			}
		}()
	}
	wg.Wait()

	if got := allowed.Load(); got != burst {
		t.Fatalf("allowed = %d, want exactly the burst of %d", got, burst)
	}
}
