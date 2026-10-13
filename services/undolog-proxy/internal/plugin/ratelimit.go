package plugin

import (
	"context"
	"math"
	"net/http"
	"strconv"
	"sync"
	"time"

	"undolog-proxy/internal/metrics"
)

// rateLimitRejectionsMetric counts requests the limiter rejected, published
// on GET /metrics.
const rateLimitRejectionsMetric = "undolog_proxy_rate_limit_rejections_total"

type digestCtxKey string

const ctxKeyAPIKeyDigest digestCtxKey = "api_key_digest"

// WithAPIKeyDigest returns a context carrying the API key digest the limiter
// meters. The authentication middleware attaches it after the constant-time
// match, so the raw key never enters the context.
func WithAPIKeyDigest(ctx context.Context, digest string) context.Context {
	return context.WithValue(ctx, ctxKeyAPIKeyDigest, digest)
}

// APIKeyDigestFrom returns the digest identity the authentication middleware
// stored, or an empty string when the request carries none.
func APIKeyDigestFrom(ctx context.Context) string {
	if digest, ok := ctx.Value(ctxKeyAPIKeyDigest).(string); ok {
		return digest
	}
	return ""
}

// rateLimiter meters each resolved API key with its own token bucket, which
// is the proxy's only source of 429. The map is bounded by the number of
// configured API keys because only authenticated digests reach the chain.
type rateLimiter struct {
	mu         sync.Mutex
	rps        float64
	burst      float64
	buckets    map[string]*tokenBucket
	rejections *metrics.Counter
}

// tokenBucket holds one key's remaining capacity and the time of its last
// refill.
type tokenBucket struct {
	tokens     float64
	refilledAt time.Time
}

// NewRateLimiter builds the per-API-key limiter. rps is the sustained refill
// rate in requests per second and burst the bucket size; LoadConfig enforces
// that both are positive or both are zero, because a positive rate with a
// zero bucket would reject every request. reg must not be nil: it is where
// rejections are counted for /metrics. The proxy mounts the limiter when
// the rate is configured, independently of UNDOLOG_PROXY_PLUGINS, so it
// always ships first in the chain.
func NewRateLimiter(rps float64, burst int, reg *metrics.Registry) Plugin {
	return &rateLimiter{
		rps:        rps,
		burst:      float64(burst),
		buckets:    make(map[string]*tokenBucket),
		rejections: reg.Counter(rateLimitRejectionsMetric, "Requests rejected by the per-key rate limiter"),
	}
}

// Name identifies the limiter in rejection log lines; it is mounted by
// configuration and is not selectable through UNDOLOG_PROXY_PLUGINS.
func (l *rateLimiter) Name() string { return "ratelimit" }

// Before consumes one token from the request's key bucket. An exhausted
// bucket answers 429 with Retry-After holding the wait in whole seconds.
// Requests without a digest identity are not metered: the chain mounts after
// authentication, so only callers that skipped auth can reach that state.
func (l *rateLimiter) Before(r *http.Request) Decision {
	identity := APIKeyDigestFrom(r.Context())
	if identity == "" {
		return Decision{}
	}
	wait, allowed := l.allow(identity, time.Now())
	if allowed {
		return Decision{}
	}
	l.rejections.Add(1)
	return Decision{
		Status: http.StatusTooManyRequests,
		Reason: "rate limit exceeded",
		Headers: map[string]string{
			"Retry-After": strconv.Itoa(wait),
		},
	}
}

// After leaves the response untouched: the limiter only meters requests, and
// the interface requires the hook to exist.
func (l *rateLimiter) After(http.ResponseWriter, *http.Request) {}

// SetLimit applies a new sustained rate and bucket size to every key at its
// next request. Existing tokens clamp to the new bucket size immediately, so
// a lowered burst cannot be spent down from the old one, and a zero rate
// disables metering exactly as it does at startup. The watcher calls it with
// a pair LoadConfig has already validated as both zero or both positive.
func (l *rateLimiter) SetLimit(rps float64, burst int) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.rps = rps
	l.burst = float64(burst)
	for _, b := range l.buckets {
		b.tokens = min(b.tokens, l.burst)
	}
}

// allow takes one token for identity at the given time and reports the wait
// in whole seconds when the bucket is empty. Refill is lazy per request, so
// an idle key always starts at full burst. A zero rate disables metering:
// every request passes, matching an unset limit at startup.
func (l *rateLimiter) allow(identity string, now time.Time) (int, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.rps <= 0 {
		return 0, true
	}
	b, ok := l.buckets[identity]
	if !ok {
		b = &tokenBucket{tokens: l.burst, refilledAt: now}
		l.buckets[identity] = b
	} else if elapsed := now.Sub(b.refilledAt); elapsed > 0 {
		b.tokens = min(l.burst, b.tokens+elapsed.Seconds()*l.rps)
		b.refilledAt = now
	}
	if b.tokens >= 1 {
		b.tokens--
		return 0, true
	}
	wait := math.Ceil((1 - b.tokens) / l.rps)
	if wait < 1 {
		wait = 1
	}
	return int(wait), false
}
