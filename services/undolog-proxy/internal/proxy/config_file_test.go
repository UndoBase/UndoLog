package proxy

import (
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// allConfigEnvKeys lists every environment variable LoadConfig reads, so a
// test can clear the ambient environment and prove file-only behavior.
var allConfigEnvKeys = []string{
	"UNDOLOG_PROXY_CONFIG",
	"UNDOLOG_PROXY_LISTEN_ADDR",
	"UNDOLOG_PROXY_READ_TIMEOUT_SECS",
	"UNDOLOG_PROXY_WRITE_TIMEOUT_SECS",
	"UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS",
	"UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS",
	"UNDOLOG_PROXY_ENGINE_RETRY_MAX_ATTEMPTS",
	"UNDOLOG_PROXY_ENGINE_RETRY_BASE_MS",
	"UNDOLOG_PROXY_DASHBOARD_EVENT_CHAN_SIZE",
	"UNDOLOG_PROXY_APPROVAL_RECONCILE_INTERVAL_SECS",
	"UNDOLOG_PROXY_APPROVAL_RETENTION_SECS",
	"UNDOLOG_PROXY_ENGINE_GRPC_ADDR",
	"UNDOLOG_PROXY_ENGINE_AUTH_TOKEN",
	"UNDOLOG_PROXY_MAX_ENGINE_RPCS",
	"UNDOLOG_PROXY_ENGINE_QUEUE_MAX",
	"UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS",
	"UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD",
	"UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS",
	"UNDOLOG_PROXY_UPSTREAM_TOOL_URL",
	"UNDOLOG_LOG_LEVEL",
	"UNDOLOG_PROXY_API_KEYS",
	"UNDOLOG_PROXY_PLUGINS",
	"UNDOLOG_PROXY_RATE_LIMIT_RPS",
	"UNDOLOG_PROXY_RATE_LIMIT_BURST",
	"UNDOLOG_PROXY_MAX_BODY_BYTES",
	"UNDOLOG_PROXY_READ_HEADER_TIMEOUT_SECS",
	"UNDOLOG_PROXY_IDLE_TIMEOUT_SECS",
	"UNDOLOG_PROXY_MAX_HEADER_BYTES",
}

// clearConfigEnv unsets every configuration variable for one test.
func clearConfigEnv(t *testing.T) {
	t.Helper()
	for _, key := range allConfigEnvKeys {
		t.Setenv(key, "")
	}
}

// writeConfigFile stores content as the test's undolog.toml and returns its
// path.
func writeConfigFile(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "undolog.toml")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write config file: %v", err)
	}
	return path
}

// TestConfigFileDefinesEverySetting verifies a file alone configures every
// setting LoadConfig reads, including the api_keys table, the plugin list,
// and whitespace trimming on the shared secret.
func TestConfigFileDefinesEverySetting(t *testing.T) {
	clearConfigEnv(t)
	path := writeConfigFile(t, `
listen_addr = ":9999"
read_timeout_secs = 11
write_timeout_secs = 12
shutdown_timeout_secs = 13
request_timeout_secs = 14
engine_retry_max_attempts = 7
engine_retry_base_ms = 250
dashboard_event_chan_size = 64
approval_reconcile_interval_secs = 45
approval_retention_secs = 3600
engine_grpc_addr = "engine:60051"
engine_auth_token = "  file-secret  "
max_engine_rpcs = 8
engine_queue_max = 16
engine_queue_wait_ms = 250
engine_breaker_threshold = 5
engine_breaker_reset_secs = 30
upstream_tool_url = "http://upstream:9000"
log_level = "debug"
rate_limit_rps = 25.5
rate_limit_burst = 50
max_body_bytes = 4096
read_header_timeout_secs = 7
idle_timeout_secs = 90
max_header_bytes = 8192
plugins = ["header", "auth"]

[api_keys]
"key-file" = "org-file"
`)
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ListenAddr != ":9999" {
		t.Errorf("ListenAddr = %q, want %q", cfg.ListenAddr, ":9999")
	}
	if cfg.ReadTimeout != 11*time.Second {
		t.Errorf("ReadTimeout = %v, want 11s", cfg.ReadTimeout)
	}
	if cfg.WriteTimeout != 12*time.Second {
		t.Errorf("WriteTimeout = %v, want 12s", cfg.WriteTimeout)
	}
	if cfg.ShutdownTimeout != 13*time.Second {
		t.Errorf("ShutdownTimeout = %v, want 13s", cfg.ShutdownTimeout)
	}
	if cfg.RequestTimeout != 14*time.Second {
		t.Errorf("RequestTimeout = %v, want 14s", cfg.RequestTimeout)
	}
	if cfg.EngineRetryMaxAttempts != 7 {
		t.Errorf("EngineRetryMaxAttempts = %d, want 7", cfg.EngineRetryMaxAttempts)
	}
	if cfg.EngineRetryBackoff != 250*time.Millisecond {
		t.Errorf("EngineRetryBackoff = %v, want 250ms", cfg.EngineRetryBackoff)
	}
	if cfg.DashboardEventBufSize != 64 {
		t.Errorf("DashboardEventBufSize = %d, want 64", cfg.DashboardEventBufSize)
	}
	if cfg.ApprovalReconcileInterval != 45*time.Second {
		t.Errorf("ApprovalReconcileInterval = %v, want 45s", cfg.ApprovalReconcileInterval)
	}
	if cfg.ApprovalRetention != time.Hour {
		t.Errorf("ApprovalRetention = %v, want 1h", cfg.ApprovalRetention)
	}
	if cfg.EngineGRPCAddr != "engine:60051" {
		t.Errorf("EngineGRPCAddr = %q, want %q", cfg.EngineGRPCAddr, "engine:60051")
	}
	if cfg.EngineAuthToken != "file-secret" {
		t.Errorf("EngineAuthToken = %q, want %q", cfg.EngineAuthToken, "file-secret")
	}
	if cfg.MaxEngineRPCs != 8 {
		t.Errorf("MaxEngineRPCs = %d, want 8", cfg.MaxEngineRPCs)
	}
	if cfg.EngineQueueMax != 16 {
		t.Errorf("EngineQueueMax = %d, want 16", cfg.EngineQueueMax)
	}
	if cfg.EngineQueueWait != 250*time.Millisecond {
		t.Errorf("EngineQueueWait = %v, want 250ms", cfg.EngineQueueWait)
	}
	if cfg.EngineBreakerThreshold != 5 {
		t.Errorf("EngineBreakerThreshold = %d, want 5", cfg.EngineBreakerThreshold)
	}
	if cfg.EngineBreakerReset != 30*time.Second {
		t.Errorf("EngineBreakerReset = %v, want 30s", cfg.EngineBreakerReset)
	}
	if cfg.UpstreamToolURL != "http://upstream:9000" {
		t.Errorf("UpstreamToolURL = %q, want %q", cfg.UpstreamToolURL, "http://upstream:9000")
	}
	if cfg.LogLevel != "debug" {
		t.Errorf("LogLevel = %q, want %q", cfg.LogLevel, "debug")
	}
	if cfg.RateLimitRPS != 25.5 {
		t.Errorf("RateLimitRPS = %v, want 25.5", cfg.RateLimitRPS)
	}
	if cfg.RateLimitBurst != 50 {
		t.Errorf("RateLimitBurst = %d, want 50", cfg.RateLimitBurst)
	}
	if cfg.MaxBodyBytes != 4096 {
		t.Errorf("MaxBodyBytes = %d, want 4096", cfg.MaxBodyBytes)
	}
	if cfg.ReadHeaderTimeout != 7*time.Second {
		t.Errorf("ReadHeaderTimeout = %v, want 7s", cfg.ReadHeaderTimeout)
	}
	if cfg.IdleTimeout != 90*time.Second {
		t.Errorf("IdleTimeout = %v, want 90s", cfg.IdleTimeout)
	}
	if cfg.MaxHeaderBytes != 8192 {
		t.Errorf("MaxHeaderBytes = %d, want 8192", cfg.MaxHeaderBytes)
	}
	if !maps.Equal(cfg.TrustedAPIKeys, map[string]string{"key-file": "org-file"}) {
		t.Errorf("TrustedAPIKeys = %v, want the file table", cfg.TrustedAPIKeys)
	}
	if !slices.Equal(cfg.Plugins, []string{"header", "auth"}) {
		t.Errorf("Plugins = %v, want [header auth]", cfg.Plugins)
	}
}

// TestConfigFileRejectsUnknownKey verifies a key the proxy does not know
// fails startup naming the key instead of being decoded around.
func TestConfigFileRejectsUnknownKey(t *testing.T) {
	clearConfigEnv(t)
	path := writeConfigFile(t, "mystery_setting = 1\n")
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)

	_, err := LoadConfig()
	if err == nil {
		t.Fatal("an unknown key must fail startup")
	}
	if !strings.Contains(err.Error(), "mystery_setting") {
		t.Fatalf("error %v must name the unknown key", err)
	}
}

// TestEnvironmentOverridesConfigFile verifies the environment wins over the
// file for the same setting, including the whole api_keys table, while the
// file still supplies what the environment leaves unset.
func TestEnvironmentOverridesConfigFile(t *testing.T) {
	clearConfigEnv(t)
	path := writeConfigFile(t, `
listen_addr = ":7070"
read_timeout_secs = 11
engine_grpc_addr = "file-engine:50051"

[api_keys]
"key-file" = "org-file"
`)
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)
	t.Setenv("UNDOLOG_PROXY_LISTEN_ADDR", ":8081")
	t.Setenv("UNDOLOG_PROXY_READ_TIMEOUT_SECS", "22")
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "key-env=org-env")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ListenAddr != ":8081" {
		t.Errorf("ListenAddr = %q, want the environment value %q", cfg.ListenAddr, ":8081")
	}
	if cfg.ReadTimeout != 22*time.Second {
		t.Errorf("ReadTimeout = %v, want the environment value 22s", cfg.ReadTimeout)
	}
	if !maps.Equal(cfg.TrustedAPIKeys, map[string]string{"key-env": "org-env"}) {
		t.Errorf("TrustedAPIKeys = %v, want the environment table only", cfg.TrustedAPIKeys)
	}
	if cfg.EngineGRPCAddr != "file-engine:50051" {
		t.Errorf("EngineGRPCAddr = %q, want the file value the environment does not set", cfg.EngineGRPCAddr)
	}
}

// TestConfigFileAbsentKeepsDefaults verifies the environment-only path is
// unchanged: with no file selected every setting falls back as before.
func TestConfigFileAbsentKeepsDefaults(t *testing.T) {
	clearConfigEnv(t)
	t.Setenv("UNDOLOG_PROXY_API_KEYS", "key-1=org-1")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ListenAddr != ":8080" {
		t.Errorf("ListenAddr = %q, want the default %q", cfg.ListenAddr, ":8080")
	}
	if cfg.ReadTimeout != 15*time.Second {
		t.Errorf("ReadTimeout = %v, want the default 15s", cfg.ReadTimeout)
	}
	if cfg.EngineGRPCAddr != "localhost:50051" {
		t.Errorf("EngineGRPCAddr = %q, want the default", cfg.EngineGRPCAddr)
	}
	if cfg.MaxEngineRPCs != 0 || cfg.RateLimitRPS != 0 {
		t.Errorf("safety settings must stay disabled by default, got %+v", cfg)
	}
}

// TestConfigFilePartialKeepsDefaults verifies settings the file does not
// mention keep their defaults, and an explicit zero is honored as an
// intentional value instead of inheriting a default.
func TestConfigFilePartialKeepsDefaults(t *testing.T) {
	clearConfigEnv(t)
	path := writeConfigFile(t, `
listen_addr = ":7070"
read_timeout_secs = 0

[api_keys]
"key-file" = "org-file"
`)
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ListenAddr != ":7070" {
		t.Errorf("ListenAddr = %q, want the file value", cfg.ListenAddr)
	}
	if cfg.ReadTimeout != 0 {
		t.Errorf("ReadTimeout = %v, want the explicit zero", cfg.ReadTimeout)
	}
	if cfg.WriteTimeout != 15*time.Second {
		t.Errorf("WriteTimeout = %v, want the default 15s", cfg.WriteTimeout)
	}
	if cfg.MaxBodyBytes != 1<<20 {
		t.Errorf("MaxBodyBytes = %d, want the default", cfg.MaxBodyBytes)
	}
}

// TestConfigFileTrimsListEntries verifies the file's api_keys and plugins
// entries are trimmed exactly like their environment counterparts, and
// blank plugin names are dropped.
func TestConfigFileTrimsListEntries(t *testing.T) {
	clearConfigEnv(t)
	path := writeConfigFile(t, `
plugins = [" header ", "", "auth"]

[api_keys]
"  key-two  " = "  org-two  "
`)
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if !slices.Equal(cfg.Plugins, []string{"header", "auth"}) {
		t.Errorf("Plugins = %v, want trimmed names without blanks", cfg.Plugins)
	}
	if !maps.Equal(cfg.TrustedAPIKeys, map[string]string{"key-two": "org-two"}) {
		t.Errorf("TrustedAPIKeys = %v, want trimmed entries", cfg.TrustedAPIKeys)
	}
}

// TestConfigFileInvalidValuesFail verifies every invalid file fails startup
// with the file path and the offending key in the message.
func TestConfigFileInvalidValuesFail(t *testing.T) {
	cases := []struct {
		name    string
		content string
		wantKey string
	}{
		{
			name:    "syntax error names the line",
			content: "listen_addr =\n",
			wantKey: "line",
		},
		{
			name:    "wrong kind names the key",
			content: "read_timeout_secs = \"fast\"\n",
			wantKey: "read_timeout_secs",
		},
		{
			name:    "negative safety setting names the key",
			content: "max_engine_rpcs = -1\n",
			wantKey: "max_engine_rpcs",
		},
		{
			name:    "zero where positive is required names the key",
			content: "dashboard_event_chan_size = 0\n",
			wantKey: "dashboard_event_chan_size",
		},
		{
			name:    "negative rate names the key",
			content: "rate_limit_rps = -2.5\n",
			wantKey: "rate_limit_rps",
		},
		{
			name:    "empty api key entry names the entry",
			content: "[api_keys]\n\"key-blank\" = \"\"\n",
			wantKey: "key-blank",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			clearConfigEnv(t)
			path := writeConfigFile(t, tc.content)
			t.Setenv("UNDOLOG_PROXY_CONFIG", path)

			_, err := LoadConfig()
			if err == nil {
				t.Fatal("an invalid file must fail startup")
			}
			if !strings.Contains(err.Error(), path) {
				t.Fatalf("error %q must contain the file path %q", err, path)
			}
			if !strings.Contains(err.Error(), tc.wantKey) {
				t.Fatalf("error %q must name %q", err, tc.wantKey)
			}
		})
	}
}

// TestConfigFileMissingFails verifies a referenced file that does not exist
// fails startup instead of silently reverting to defaults.
func TestConfigFileMissingFails(t *testing.T) {
	clearConfigEnv(t)
	path := filepath.Join(t.TempDir(), "absent.toml")
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)

	_, err := LoadConfig()
	if err == nil {
		t.Fatal("a missing config file must fail startup")
	}
	if !strings.Contains(err.Error(), path) {
		t.Fatalf("error %q must contain the file path %q", err, path)
	}
}
