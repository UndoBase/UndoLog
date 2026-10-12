// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// This package keeps runtime configuration explicit and environment-driven so
// the proxy can be deployed safely in containers without auxiliary config files.
package proxy

import (
	"fmt"
	"math"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config holds all runtime settings for the proxy server.
//
// The values are sourced from environment variables and are intentionally kept
// small and explicit so the proxy can be configured in containers without
// auxiliary config files.
type Config struct {
	// ListenAddr is the HTTP bind address used by the proxy server.
	ListenAddr string
	// ReadTimeout limits how long the server waits for request headers.
	ReadTimeout time.Duration
	// WriteTimeout limits how long the server waits while writing responses.
	WriteTimeout time.Duration
	// ShutdownTimeout limits how long graceful shutdown may take.
	ShutdownTimeout time.Duration
	// RequestTimeout limits tool execution and engine RPC calls.
	RequestTimeout time.Duration
	// EngineRetryMaxAttempts is the maximum number of attempts for transient
	// Commit/Fail RPC failures.
	EngineRetryMaxAttempts int
	// EngineRetryBackoff is the base delay (scaled per attempt) between engine
	// connection and Commit/Fail retries.
	EngineRetryBackoff time.Duration
	// DashboardEventBufSize is the per-organization SSE channel buffer size.
	DashboardEventBufSize int
	// ApprovalReconcileInterval is how often the proxy re-syncs its approval
	// view with the engine and sweeps the in-memory store.
	ApprovalReconcileInterval time.Duration
	// ApprovalRetention is how long resolved or stale approvals stay in the
	// in-memory store before the reconciler sweeps them.
	ApprovalRetention time.Duration
	// EngineGRPCAddr is the Rust engine gRPC endpoint.
	EngineGRPCAddr string
	// EngineAuthToken is the shared secret presented to the engine on every
	// gRPC call. Surrounding whitespace is trimmed, as the engine trims its
	// copy, and an empty value sends no credential. It must never be logged.
	EngineAuthToken string
	// MaxEngineRPCs caps engine RPCs in flight. Zero disables the gate.
	MaxEngineRPCs int
	// EngineQueueMax bounds how many engine RPCs may wait for a gate slot.
	// It must be set together with EngineQueueWait and requires MaxEngineRPCs.
	EngineQueueMax int
	// EngineQueueWait is the budget one engine RPC may wait for a slot.
	EngineQueueWait time.Duration
	// EngineBreakerThreshold is the run of consecutive engine transport
	// failures that opens the breaker. It must be set together with
	// EngineBreakerReset.
	EngineBreakerThreshold int
	// EngineBreakerReset is how long an open breaker waits before probing
	// the engine again.
	EngineBreakerReset time.Duration
	// UpstreamToolURL is the HTTP endpoint that receives forwarded tool calls.
	UpstreamToolURL string
	// LogLevel controls proxy logging verbosity.
	LogLevel string
	// TrustedAPIKeys maps API keys to organization identifiers.
	TrustedAPIKeys map[string]string
	// Plugins is the ordered interceptor plugin names from
	// UNDOLOG_PROXY_PLUGINS. An empty list runs no plugins.
	Plugins []string
	// RateLimitRPS is the sustained request rate allowed per API key in
	// requests per second. Zero disables the limiter.
	RateLimitRPS float64
	// RateLimitBurst is the token bucket size per API key. It must be set
	// together with RateLimitRPS.
	RateLimitBurst int
	// MaxBodyBytes is the maximum size of a request body the proxy decodes.
	MaxBodyBytes int64
	// ReadHeaderTimeout limits how long the server waits to read request headers.
	ReadHeaderTimeout time.Duration
	// IdleTimeout limits how long a keep-alive connection may sit idle between
	// requests before the server closes it.
	IdleTimeout time.Duration
	// MaxHeaderBytes limits the total size of request headers the server parses.
	MaxHeaderBytes int
}

// LoadConfig reads proxy settings from environment variables and applies sane defaults.
func LoadConfig() (Config, error) {
	bp, err := backpressureEnv()
	if err != nil {
		return Config{}, err
	}
	rateRPS, rateBurst, err := rateLimitEnv()
	if err != nil {
		return Config{}, err
	}
	cfg := Config{
		ListenAddr:                getenv("UNDOLOG_PROXY_LISTEN_ADDR", ":8080"),
		ReadTimeout:               durationEnv("UNDOLOG_PROXY_READ_TIMEOUT_SECS", 15*time.Second),
		WriteTimeout:              durationEnv("UNDOLOG_PROXY_WRITE_TIMEOUT_SECS", 15*time.Second),
		ShutdownTimeout:           durationEnv("UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS", 30*time.Second),
		RequestTimeout:            durationEnv("UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS", 30*time.Second),
		EngineRetryMaxAttempts:    intEnv("UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS", 3),
		EngineRetryBackoff:        millisEnv("UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS", 100*time.Millisecond),
		DashboardEventBufSize:     intEnv("UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE", 128),
		ApprovalReconcileInterval: durationEnv("UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS", 60*time.Second),
		ApprovalRetention:         durationEnv("UNDOLOG_PROXY_APPROVAL_RETENTION_SECS", 24*time.Hour),
		EngineGRPCAddr:            getenv("UNDOLOG_PROXY_ENGINE_GRPC_ADDR", "localhost:50051"),
		EngineAuthToken:           strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_ENGINE_AUTH_TOKEN")),
		MaxEngineRPCs:             bp.maxRPCs,
		EngineQueueMax:            bp.queueMax,
		EngineQueueWait:           bp.queueWait,
		EngineBreakerThreshold:    bp.breakerThreshold,
		EngineBreakerReset:        bp.breakerReset,
		UpstreamToolURL:           getenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", ""),
		LogLevel:                  getenv("UNDOLOG_LOG_LEVEL", "info"),
		TrustedAPIKeys:            parseAPIKeys(os.Getenv("UNDOLOG_PROXY_API_KEYS")),
		Plugins:                   parsePlugins(os.Getenv("UNDOLOG_PROXY_PLUGINS")),
		RateLimitRPS:              rateRPS,
		RateLimitBurst:            rateBurst,
		MaxBodyBytes:              int64Env("UNDOLOG_PROXY_MAX_BODY_BYTES", 1<<20),
		ReadHeaderTimeout:         durationEnv("UNDOLOG_PROXY_READ_HEADER_TIMEOUT_SECS", 15*time.Second),
		IdleTimeout:               durationEnv("UNDOLOG_PROXY_IDLE_TIMEOUT_SECS", 60*time.Second),
		MaxHeaderBytes:            intEnv("UNDOLOG_PROXY_MAX_HEADER_BYTES", 1<<20),
	}

	if cfg.ListenAddr == "" {
		return Config{}, fmt.Errorf("listen address cannot be empty")
	}
	if cfg.EngineGRPCAddr == "" {
		return Config{}, fmt.Errorf("engine gRPC address cannot be empty")
	}
	if len(cfg.TrustedAPIKeys) == 0 {
		return Config{}, fmt.Errorf("UNDOLOG_PROXY_API_KEYS must define at least one key=org pair")
	}
	if cfg.UpstreamToolURL != "" {
		u, err := url.Parse(cfg.UpstreamToolURL)
		if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
			return Config{}, fmt.Errorf("UNDOLOG_PROXY_UPSTREAM_TOOL_URL must be an absolute http(s) URL, got %q", cfg.UpstreamToolURL)
		}
	}
	if (cfg.RateLimitRPS > 0) != (cfg.RateLimitBurst > 0) {
		return Config{}, fmt.Errorf("UNDOLOG_PROXY_RATE_LIMIT_RPS and UNDOLOG_PROXY_RATE_LIMIT_BURST must be set together")
	}
	if (cfg.EngineQueueMax > 0) != (cfg.EngineQueueWait > 0) {
		return Config{}, fmt.Errorf("UNDOLOG_PROXY_ENGINE_QUEUE_MAX and UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS must be set together")
	}
	if cfg.EngineQueueMax > 0 && cfg.MaxEngineRPCs == 0 {
		return Config{}, fmt.Errorf("UNDOLOG_PROXY_ENGINE_QUEUE_MAX requires UNDOLOG_PROXY_MAX_ENGINE_RPCS")
	}
	if (cfg.EngineBreakerThreshold > 0) != (cfg.EngineBreakerReset > 0) {
		return Config{}, fmt.Errorf("UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD and UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS must be set together")
	}
	return cfg, nil
}

// backpressureSettings holds the raw engine backpressure values.
type backpressureSettings struct {
	maxRPCs          int
	queueMax         int
	queueWait        time.Duration
	breakerThreshold int
	breakerReset     time.Duration
}

// backpressureEnv reads the engine backpressure settings. Each mechanism is
// optional and zero disables it, but a malformed or negative value fails
// startup instead of silently dropping a configured limit, because a typo in
// a safety setting must not disable it. LoadConfig validates which settings
// must be set together.
func backpressureEnv() (backpressureSettings, error) {
	var s backpressureSettings
	var err error
	if s.maxRPCs, err = strictIntEnv("UNDOLOG_PROXY_MAX_ENGINE_RPCS"); err != nil {
		return s, err
	}
	if s.queueMax, err = strictIntEnv("UNDOLOG_PROXY_ENGINE_QUEUE_MAX"); err != nil {
		return s, err
	}
	if s.queueWait, err = strictMsEnv("UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS"); err != nil {
		return s, err
	}
	if s.breakerThreshold, err = strictIntEnv("UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD"); err != nil {
		return s, err
	}
	if s.breakerReset, err = strictSecsEnv("UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS"); err != nil {
		return s, err
	}
	return s, nil
}

// strictIntEnv reads a non-negative integer setting, treating an unset value
// as zero and failing on anything else a safety setting cannot use.
func strictIntEnv(key string) (int, error) {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return 0, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil || v < 0 {
		return 0, fmt.Errorf("%s must be a non-negative integer, got %q", key, raw)
	}
	return v, nil
}

// strictMsEnv reads a non-negative millisecond setting as a duration.
func strictMsEnv(key string) (time.Duration, error) {
	v, err := strictIntEnv(key)
	if err != nil {
		return 0, err
	}
	return time.Duration(v) * time.Millisecond, nil
}

// strictSecsEnv reads a non-negative seconds setting as a duration.
func strictSecsEnv(key string) (time.Duration, error) {
	v, err := strictIntEnv(key)
	if err != nil {
		return 0, err
	}
	return time.Duration(v) * time.Second, nil
}

// rateLimitEnv reads the per-key rate limit pair. Both settings are optional
// and zero disables the limiter, but a malformed or negative value fails
// startup instead of silently dropping a configured limit, because a typo in
// a safety setting must not disable it.
func rateLimitEnv() (float64, int, error) {
	var rps float64
	if raw := strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_RATE_LIMIT_RPS")); raw != "" {
		v, err := strconv.ParseFloat(raw, 64)
		if err != nil || v < 0 || math.IsNaN(v) || math.IsInf(v, 0) {
			return 0, 0, fmt.Errorf("UNDOLOG_PROXY_RATE_LIMIT_RPS must be a non-negative number, got %q", raw)
		}
		rps = v
	}
	var burst int
	if raw := strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_RATE_LIMIT_BURST")); raw != "" {
		v, err := strconv.Atoi(raw)
		if err != nil || v < 0 {
			return 0, 0, fmt.Errorf("UNDOLOG_PROXY_RATE_LIMIT_BURST must be a non-negative integer, got %q", raw)
		}
		burst = v
	}
	return rps, burst, nil
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func durationEnv(key string, fallback time.Duration) time.Duration {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	secs, err := strconv.Atoi(raw)
	if err != nil || secs < 0 {
		return fallback
	}
	return time.Duration(secs) * time.Second
}

func millisEnv(key string, fallback time.Duration) time.Duration {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	ms, err := strconv.Atoi(raw)
	if err != nil || ms < 0 {
		return fallback
	}
	return time.Duration(ms) * time.Millisecond
}

func intEnv(key string, fallback int) int {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return fallback
	}
	return n
}

func int64Env(key string, fallback int64) int64 {
	raw := os.Getenv(key)
	if raw == "" {
		return fallback
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || n <= 0 {
		return fallback
	}
	return n
}

func parseAPIKeys(raw string) map[string]string {
	out := make(map[string]string)
	if strings.TrimSpace(raw) == "" {
		return out
	}
	for _, pair := range strings.Split(raw, ",") {
		kv := strings.SplitN(strings.TrimSpace(pair), "=", 2)
		if len(kv) != 2 {
			continue
		}
		key := strings.TrimSpace(kv[0])
		org := strings.TrimSpace(kv[1])
		if key != "" && org != "" {
			out[key] = org
		}
	}
	return out
}

// parsePlugins splits the comma-separated plugin order, trimming each name
// and dropping blanks so "header,, second" and " header " both configure
// cleanly. The order is preserved: it is the execution order.
func parsePlugins(raw string) []string {
	var out []string
	if strings.TrimSpace(raw) == "" {
		return out
	}
	for _, name := range strings.Split(raw, ",") {
		if trimmed := strings.TrimSpace(name); trimmed != "" {
			out = append(out, trimmed)
		}
	}
	return out
}
