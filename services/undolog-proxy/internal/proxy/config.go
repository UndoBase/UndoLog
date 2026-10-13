// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// This package keeps runtime configuration explicit and environment-driven,
// with an optional TOML file for operators who prefer one; environment
// variables always override file values, so container deployments stay
// fully environment-driven.
package proxy

import (
	"fmt"
	"maps"
	"math"
	"net/url"
	"os"
	"slices"
	"strconv"
	"strings"
	"time"
)

// envKeyConfigFile names the environment variable that selects the optional
// TOML configuration file.
const envKeyConfigFile = "UNDOLOG_PROXY_CONFIG"

// Config holds all runtime settings for the proxy server.
//
// The values are sourced from environment variables first, then an optional
// undolog.toml selected by UNDOLOG_PROXY_CONFIG, then built-in defaults, and
// are intentionally kept small and explicit so the proxy can be configured
// in containers without auxiliary config files.
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
	// filePath is the configuration file this Config was loaded from, empty
	// when the proxy runs from environment and defaults alone. The watcher
	// polls it; it is an input artifact, not a setting, so it stays
	// unexported.
	filePath string
}

// LoadConfig reads proxy settings from environment variables, an optional
// undolog.toml selected by UNDOLOG_PROXY_CONFIG, and built-in defaults, in
// that precedence order. The watcher re-runs it on every file change, so a
// reload sees the same precedence and validation a restart would.
func LoadConfig() (Config, error) {
	f, err := loadFileConfig(envKeyConfigFile)
	if err != nil {
		return Config{}, err
	}
	bp, err := resolveBackpressure(f)
	if err != nil {
		return Config{}, err
	}
	rateRPS, rateBurst, err := resolveRateLimit(f)
	if err != nil {
		return Config{}, err
	}
	cfg := Config{
		ListenAddr:                resolveStr("UNDOLOG_PROXY_LISTEN_ADDR", f.ListenAddr, ":8080"),
		ReadTimeout:               resolveSecs("UNDOLOG_PROXY_READ_TIMEOUT_SECS", f.ReadTimeoutSecs, 15*time.Second),
		WriteTimeout:              resolveSecs("UNDOLOG_PROXY_WRITE_TIMEOUT_SECS", f.WriteTimeoutSecs, 15*time.Second),
		ShutdownTimeout:           resolveSecs("UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS", f.ShutdownTimeoutSecs, 30*time.Second),
		RequestTimeout:            resolveSecs("UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS", f.RequestTimeoutSecs, 30*time.Second),
		EngineRetryMaxAttempts:    resolveInt("UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS", f.EngineRetryMaxAttempts, 3),
		EngineRetryBackoff:        resolveMillis("UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS", f.EngineRetryBaseMs, 100*time.Millisecond),
		DashboardEventBufSize:     resolveInt("UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE", f.DashboardEventChanSize, 128),
		ApprovalReconcileInterval: resolveSecs("UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS", f.ApprovalReconcileIntervalSecs, 60*time.Second),
		ApprovalRetention:         resolveSecs("UNDOLOG_PROXY_APPROVAL_RETENTION_SECS", f.ApprovalRetentionSecs, 24*time.Hour),
		EngineGRPCAddr:            resolveStr("UNDOLOG_PROXY_ENGINE_GRPC_ADDR", f.EngineGRPCAddr, "localhost:50051"),
		EngineAuthToken:           resolveTrimmed("UNDOLOG_PROXY_ENGINE_AUTH_TOKEN", f.EngineAuthToken),
		MaxEngineRPCs:             bp.maxRPCs,
		EngineQueueMax:            bp.queueMax,
		EngineQueueWait:           bp.queueWait,
		EngineBreakerThreshold:    bp.breakerThreshold,
		EngineBreakerReset:        bp.breakerReset,
		UpstreamToolURL:           resolveStr("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", f.UpstreamToolURL, ""),
		LogLevel:                  resolveStr("UNDOLOG_LOG_LEVEL", f.LogLevel, "info"),
		TrustedAPIKeys:            resolveAPIKeys(f),
		Plugins:                   resolvePlugins(f),
		RateLimitRPS:              rateRPS,
		RateLimitBurst:            rateBurst,
		MaxBodyBytes:              resolveInt64("UNDOLOG_PROXY_MAX_BODY_BYTES", f.MaxBodyBytes, 1<<20),
		ReadHeaderTimeout:         resolveSecs("UNDOLOG_PROXY_READ_HEADER_TIMEOUT_SECS", f.ReadHeaderTimeoutSecs, 15*time.Second),
		IdleTimeout:               resolveSecs("UNDOLOG_PROXY_IDLE_TIMEOUT_SECS", f.IdleTimeoutSecs, 60*time.Second),
		MaxHeaderBytes:            resolveInt("UNDOLOG_PROXY_MAX_HEADER_BYTES", f.MaxHeaderBytes, 1<<20),
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
	cfg.filePath = strings.TrimSpace(os.Getenv(envKeyConfigFile))
	return cfg, nil
}

// restartOnlyNames returns the file keys of the settings that differ between
// the running configuration and a reloaded one but cannot be applied live,
// because they shape the already built listener, engine client, or plugin
// chain. The order is stable so the same change always logs the same list,
// and only key names appear, never values: one of them is a secret.
func restartOnlyNames(running, next Config) []string {
	var out []string
	add := func(name string, changed bool) {
		if changed {
			out = append(out, name)
		}
	}
	add("listen_addr", running.ListenAddr != next.ListenAddr)
	add("read_timeout_secs", running.ReadTimeout != next.ReadTimeout)
	add("write_timeout_secs", running.WriteTimeout != next.WriteTimeout)
	add("shutdown_timeout_secs", running.ShutdownTimeout != next.ShutdownTimeout)
	add("request_timeout_secs", running.RequestTimeout != next.RequestTimeout)
	add("engine_retry_max_attempts", running.EngineRetryMaxAttempts != next.EngineRetryMaxAttempts)
	add("engine_retry_base_ms", running.EngineRetryBackoff != next.EngineRetryBackoff)
	add("dashboard_event_chan_size", running.DashboardEventBufSize != next.DashboardEventBufSize)
	add("approval_reconcile_interval_secs", running.ApprovalReconcileInterval != next.ApprovalReconcileInterval)
	add("approval_retention_secs", running.ApprovalRetention != next.ApprovalRetention)
	add("engine_grpc_addr", running.EngineGRPCAddr != next.EngineGRPCAddr)
	add("engine_auth_token", running.EngineAuthToken != next.EngineAuthToken)
	add("max_engine_rpcs", running.MaxEngineRPCs != next.MaxEngineRPCs)
	add("engine_queue_max", running.EngineQueueMax != next.EngineQueueMax)
	add("engine_queue_wait_ms", running.EngineQueueWait != next.EngineQueueWait)
	add("engine_breaker_threshold", running.EngineBreakerThreshold != next.EngineBreakerThreshold)
	add("engine_breaker_reset_secs", running.EngineBreakerReset != next.EngineBreakerReset)
	add("upstream_tool_url", running.UpstreamToolURL != next.UpstreamToolURL)
	add("api_keys", !maps.Equal(running.TrustedAPIKeys, next.TrustedAPIKeys))
	add("plugins", !slices.Equal(running.Plugins, next.Plugins))
	add("max_body_bytes", running.MaxBodyBytes != next.MaxBodyBytes)
	add("read_header_timeout_secs", running.ReadHeaderTimeout != next.ReadHeaderTimeout)
	add("idle_timeout_secs", running.IdleTimeout != next.IdleTimeout)
	add("max_header_bytes", running.MaxHeaderBytes != next.MaxHeaderBytes)
	return out
}

// backpressureSettings holds the raw engine backpressure values.
type backpressureSettings struct {
	maxRPCs          int
	queueMax         int
	queueWait        time.Duration
	breakerThreshold int
	breakerReset     time.Duration
}

// resolveBackpressure reads the engine backpressure settings from the
// environment first and the config file second. Each mechanism is optional
// and zero disables it, but a malformed or negative environment value fails
// startup instead of silently dropping a configured limit, because a typo
// in a safety setting must not disable it. LoadConfig validates which
// settings must be set together.
func resolveBackpressure(f *fileLayer) (backpressureSettings, error) {
	var s backpressureSettings
	var err error
	if s.maxRPCs, err = strictIntSetting("UNDOLOG_PROXY_MAX_ENGINE_RPCS", f.MaxEngineRPCs); err != nil {
		return s, err
	}
	if s.queueMax, err = strictIntSetting("UNDOLOG_PROXY_ENGINE_QUEUE_MAX", f.EngineQueueMax); err != nil {
		return s, err
	}
	if s.queueWait, err = strictMsSetting("UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS", f.EngineQueueWaitMs); err != nil {
		return s, err
	}
	if s.breakerThreshold, err = strictIntSetting("UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD", f.EngineBreakerThreshold); err != nil {
		return s, err
	}
	if s.breakerReset, err = strictSecsSetting("UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS", f.EngineBreakerResetSecs); err != nil {
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

// resolveRateLimit reads the per-key rate limit pair from the environment
// first and the config file second. Both settings are optional and zero
// disables the limiter, but a malformed or negative environment value fails
// startup instead of silently dropping a configured limit, because a typo
// in a safety setting must not disable it.
func resolveRateLimit(f *fileLayer) (float64, int, error) {
	var rps float64
	if raw := strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_RATE_LIMIT_RPS")); raw != "" {
		v, err := strconv.ParseFloat(raw, 64)
		if err != nil || v < 0 || math.IsNaN(v) || math.IsInf(v, 0) {
			return 0, 0, fmt.Errorf("UNDOLOG_PROXY_RATE_LIMIT_RPS must be a non-negative number, got %q", raw)
		}
		rps = v
	} else if f.RateLimitRPS != nil {
		rps = float64(*f.RateLimitRPS)
	}
	var burst int
	if raw := strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_RATE_LIMIT_BURST")); raw != "" {
		v, err := strconv.Atoi(raw)
		if err != nil || v < 0 {
			return 0, 0, fmt.Errorf("UNDOLOG_PROXY_RATE_LIMIT_BURST must be a non-negative integer, got %q", raw)
		}
		burst = v
	} else if f.RateLimitBurst != nil {
		burst = *f.RateLimitBurst
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
