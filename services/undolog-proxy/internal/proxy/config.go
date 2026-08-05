// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// This package keeps runtime configuration explicit and environment-driven so
// the proxy can be deployed safely in containers without auxiliary config files.
package proxy

import (
	"fmt"
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
	// UpstreamToolURL is the HTTP endpoint that receives forwarded tool calls.
	UpstreamToolURL string
	// LogLevel controls proxy logging verbosity.
	LogLevel string
	// TrustedAPIKeys maps API keys to organization identifiers.
	TrustedAPIKeys map[string]string
}

// LoadConfig reads proxy settings from environment variables and applies sane defaults.
func LoadConfig() (Config, error) {
	cfg := Config{
		ListenAddr:                getenv("UNDOLOG_PROXY_LISTEN_ADDR", ":8080"),
		ReadTimeout:               durationEnv("UNDOLOG_PROXY_READ_TIMEOUT_SECS", 15*time.Second),
		WriteTimeout:              durationEnv("UNDOLOG_PROXY_WRITE_TIMEOUT_SECS", 15*time.Second),
		ShutdownTimeout:           durationEnv("UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS", 30*time.Second),
		RequestTimeout:            durationEnv("UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS", 30*time.Second),
		DashboardEventBufSize:     intEnv("UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE", 128),
		ApprovalReconcileInterval: durationEnv("UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS", 60*time.Second),
		ApprovalRetention:         durationEnv("UNDOLOG_PROXY_APPROVAL_RETENTION_SECS", 24*time.Hour),
		EngineGRPCAddr:            getenv("UNDOLOG_PROXY_ENGINE_GRPC_ADDR", "localhost:50051"),
		UpstreamToolURL:           getenv("UNDOLOG_PROXY_UPSTREAM_TOOL_URL", ""),
		LogLevel:                  getenv("UNDOLOG_LOG_LEVEL", "info"),
		TrustedAPIKeys:            parseAPIKeys(os.Getenv("UNDOLOG_PROXY_API_KEYS")),
	}

	if cfg.ListenAddr == "" {
		return Config{}, fmt.Errorf("listen address cannot be empty")
	}
	if cfg.EngineGRPCAddr == "" {
		return Config{}, fmt.Errorf("engine gRPC address cannot be empty")
	}
	return cfg, nil
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
