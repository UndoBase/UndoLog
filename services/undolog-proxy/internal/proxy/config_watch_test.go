package proxy

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
)

// syncBuffer serializes log writes from the watcher goroutine with reads
// from the test goroutine.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

// Write appends to the buffer under the lock, satisfying io.Writer for a
// slog handler.
func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

// String returns the accumulated log lines under the lock, so a test can
// read them while the watcher goroutine writes.
func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// testLogger returns a debug-level logger writing into buf, so a test can
// assert on every line a watcher or server emits.
func testLogger(buf *syncBuffer) *slog.Logger {
	return slog.New(slog.NewTextHandler(buf, &slog.HandlerOptions{Level: slog.LevelDebug}))
}

// configPath returns a fresh path in the test's temp dir for a file the
// test rewrites in place.
func configPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "undolog.toml")
}

// rewriteConfigFile replaces the file's content so the modification time
// and size move together, as an operator's edit would.
func rewriteConfigFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("rewrite config file: %v", err)
	}
}

// useConfigFile clears every configuration variable and points
// UNDOLOG_PROXY_CONFIG at path, so a reload sees only this file.
func useConfigFile(t *testing.T, path string) {
	t.Helper()
	clearConfigEnv(t)
	t.Setenv("UNDOLOG_PROXY_CONFIG", path)
}

// configWith returns a valid file version with one value from each
// reloadable group. Any two versions differ in size, so a change stays
// detectable on a filesystem with coarse modification time resolution.
func configWith(logLevel string, rps, burst int) string {
	return fmt.Sprintf(`log_level = %q
rate_limit_rps = %d
rate_limit_burst = %d

[api_keys]
"key-1" = "org-1"
`, logLevel, rps, burst)
}

// baseConfig is the version a watcher starts from: the API key table
// LoadConfig requires plus one value from each reloadable group.
var baseConfig = configWith("info", 10, 20)

// waitFor polls condition until it holds or the deadline passes.
func waitFor(t *testing.T, condition func() bool, message string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", message)
}

// appliedRecorder captures the configurations a watcher reloaded, safe to
// read while the watcher goroutine records.
type appliedRecorder struct {
	mu      sync.Mutex
	configs []Config
}

// record stores one applied configuration.
func (r *appliedRecorder) record(next Config) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.configs = append(r.configs, next)
}

// count returns how many configurations have been applied so far.
func (r *appliedRecorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.configs)
}

// last returns the most recently applied configuration, or false when none
// has been.
func (r *appliedRecorder) last() (Config, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.configs) == 0 {
		return Config{}, false
	}
	return r.configs[len(r.configs)-1], true
}

// startWatcher runs the watcher loop on its own goroutine and returns a
// function that waits for the loop to exit, which it does once ctx is
// canceled.
func startWatcher(t *testing.T, ctx context.Context, watcher *configWatcher) func() {
	t.Helper()
	done := make(chan struct{})
	go func() {
		watcher.run(ctx)
		close(done)
	}()
	return func() {
		t.Helper()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Fatal("watcher must exit after its context is canceled")
		}
	}
}

// TestConfigWatcherAppliesNewVersion verifies the normal path: an unchanged
// file is never reloaded, and a new version applies its reloadable settings
// through the watcher's callback without a restart.
func TestConfigWatcherAppliesNewVersion(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)

	rec := &appliedRecorder{}
	watcher := newConfigWatcher(path, time.Millisecond, testLogger(&syncBuffer{}), rec.record)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	stop := startWatcher(t, ctx, watcher)

	time.Sleep(30 * time.Millisecond)
	if got := rec.count(); got != 0 {
		t.Fatalf("reloaded %d times for an unchanged file, want 0", got)
	}

	rewriteConfigFile(t, path, configWith("debug", 25, 40))
	waitFor(t, func() bool { return rec.count() == 1 }, "the new version to apply")

	got, ok := rec.last()
	if !ok {
		t.Fatal("a reload must have been recorded")
	}
	if got.LogLevel != "debug" {
		t.Fatalf("log level = %q, want the reloaded debug", got.LogLevel)
	}
	if got.RateLimitRPS != 25 || got.RateLimitBurst != 40 {
		t.Fatalf("rate limit = %v/%d, want the reloaded 25/40", got.RateLimitRPS, got.RateLimitBurst)
	}
	cancel()
	stop()
}

// TestConfigWatcherKeepsLastGoodOnMalformedFile verifies a version that
// fails validation is rejected whole: no reload runs for it, the failure is
// logged, and the next valid version applies.
func TestConfigWatcherKeepsLastGoodOnMalformedFile(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)

	buf := &syncBuffer{}
	rec := &appliedRecorder{}
	watcher := newConfigWatcher(path, time.Millisecond, testLogger(buf), rec.record)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	stop := startWatcher(t, ctx, watcher)

	// An unknown key is rejected the same way a parse error is, so a
	// typo can never be silently replaced by the last good file.
	rewriteConfigFile(t, path, "log_levl = \"debug\"\n")
	waitFor(t, func() bool { return strings.Contains(buf.String(), "config reload failed") }, "the malformed version to be rejected")
	if got := rec.count(); got != 0 {
		t.Fatalf("reloaded %d times for a malformed file, want 0", got)
	}

	rewriteConfigFile(t, path, configWith("error", 5, 5))
	waitFor(t, func() bool { return rec.count() == 1 }, "the corrected version to apply")
	got, _ := rec.last()
	if got.LogLevel != "error" {
		t.Fatalf("log level = %q, want the corrected error", got.LogLevel)
	}
	cancel()
	stop()
}

// TestConfigWatcherWarnsOncePerUnreadableFile verifies a missing file is
// logged once per failure instead of once per poll, and the next readable
// version still applies.
func TestConfigWatcherWarnsOncePerUnreadableFile(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)

	buf := &syncBuffer{}
	rec := &appliedRecorder{}
	watcher := newConfigWatcher(path, time.Millisecond, testLogger(buf), rec.record)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	stop := startWatcher(t, ctx, watcher)

	if err := os.Remove(path); err != nil {
		t.Fatalf("remove config file: %v", err)
	}
	waitFor(t, func() bool { return strings.Contains(buf.String(), "config file unreadable") }, "the missing file to be logged")
	time.Sleep(30 * time.Millisecond)
	if got := strings.Count(buf.String(), "config file unreadable"); got != 1 {
		t.Fatalf("missing file logged %d times, want once per failure", got)
	}

	rewriteConfigFile(t, path, configWith("warn", 5, 5))
	waitFor(t, func() bool { return rec.count() == 1 }, "the restored version to apply")
	cancel()
	stop()
}

// TestConfigWatcherDetectsConfigMapSymlink verifies the Kubernetes
// ConfigMap layout: the mounted file is a symlink through ..data into a
// versioned directory, and retargeting ..data atomically is a new version
// the poller must see.
func TestConfigWatcherDetectsConfigMapSymlink(t *testing.T) {
	root := t.TempDir()
	v1 := filepath.Join(root, "..2026_01")
	v2 := filepath.Join(root, "..2026_02")
	for _, dir := range []string{v1, v2} {
		if err := os.Mkdir(dir, 0o755); err != nil {
			t.Fatalf("mkdir versioned dir: %v", err)
		}
	}
	rewriteConfigFile(t, filepath.Join(v1, "undolog.toml"), baseConfig)
	rewriteConfigFile(t, filepath.Join(v2, "undolog.toml"), configWith("debug", 5, 5))

	mount := filepath.Join(root, "undolog.toml")
	if err := os.Symlink(filepath.Join(root, "..data", "undolog.toml"), mount); err != nil {
		t.Fatalf("symlink mounted file: %v", err)
	}
	retarget := func(dir string) {
		t.Helper()
		tmp := filepath.Join(root, "..data_tmp")
		if err := os.Symlink(dir, tmp); err != nil {
			t.Fatalf("symlink data dir: %v", err)
		}
		if err := os.Rename(tmp, filepath.Join(root, "..data")); err != nil {
			t.Fatalf("retarget data dir: %v", err)
		}
	}
	retarget(v1)
	useConfigFile(t, mount)

	rec := &appliedRecorder{}
	watcher := newConfigWatcher(mount, time.Millisecond, testLogger(&syncBuffer{}), rec.record)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	stop := startWatcher(t, ctx, watcher)

	time.Sleep(30 * time.Millisecond)
	if got := rec.count(); got != 0 {
		t.Fatalf("reloaded %d times for the untouched mount, want 0", got)
	}

	retarget(v2)
	waitFor(t, func() bool { return rec.count() == 1 }, "the symlink swap to be detected")
	got, _ := rec.last()
	if got.LogLevel != "debug" || got.RateLimitRPS != 5 {
		t.Fatalf("reloaded = log level %q, rate %v, want the second version's debug and 5", got.LogLevel, got.RateLimitRPS)
	}
	cancel()
	stop()
}

// TestConfigWatcherEnvOverridesReloadedFile verifies the precedence a
// restart would see also holds on reload: an environment value is never
// replaced by the file.
func TestConfigWatcherEnvOverridesReloadedFile(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)
	t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_RPS", "77")
	t.Setenv("UNDOLOG_PROXY_RATE_LIMIT_BURST", "3")

	rec := &appliedRecorder{}
	watcher := newConfigWatcher(path, time.Millisecond, testLogger(&syncBuffer{}), rec.record)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	stop := startWatcher(t, ctx, watcher)

	rewriteConfigFile(t, path, configWith("debug", 5, 5))
	waitFor(t, func() bool { return rec.count() == 1 }, "the file change to apply")

	got, _ := rec.last()
	if got.RateLimitRPS != 77 || got.RateLimitBurst != 3 {
		t.Fatalf("rate limit = %v/%d, want the environment value 77/3", got.RateLimitRPS, got.RateLimitBurst)
	}
	if got.LogLevel != "debug" {
		t.Fatalf("log level = %q, want the file value debug", got.LogLevel)
	}
	cancel()
	stop()
}

// TestConfigWatcherStopsWithContext verifies canceling the context ends the
// poll loop: it exits cleanly and a later change is not applied.
func TestConfigWatcherStopsWithContext(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)

	rec := &appliedRecorder{}
	watcher := newConfigWatcher(path, time.Millisecond, testLogger(&syncBuffer{}), rec.record)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	stop := startWatcher(t, ctx, watcher)

	rewriteConfigFile(t, path, configWith("debug", 25, 40))
	waitFor(t, func() bool { return rec.count() == 1 }, "the change before the stop to apply")

	cancel()
	stop()

	rewriteConfigFile(t, path, configWith("error", 5, 5))
	time.Sleep(30 * time.Millisecond)
	if got := rec.count(); got != 1 {
		t.Fatalf("reloaded %d times after the stop, want only the pre-cancel one", got)
	}
}

// TestNewConfigWatcherDefaults verifies a misconfigured watcher still runs:
// a non-positive interval falls back to the production default and a nil
// logger to the standard one, and the constructor seeds the stamp the poll
// loop compares against.
func TestNewConfigWatcherDefaults(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	watcher := newConfigWatcher(path, 0, nil, func(Config) {})
	if watcher.interval != configWatchInterval {
		t.Fatalf("interval = %s, want the default %s", watcher.interval, configWatchInterval)
	}
	if watcher.logger == nil {
		t.Fatal("a nil logger must fall back to the standard one")
	}
	if watcher.stamp.size != int64(len(baseConfig)) {
		t.Fatalf("seeded stamp size = %d, want the file's %d", watcher.stamp.size, len(baseConfig))
	}
}

// TestServerApplyReloadKeepsRestartOnlySettings verifies a reload applies
// the reloadable subset to a running server and reports the rest: the
// listener, engine address, and shared secret keep their running values,
// the rate limit reaches the mounted limiter, and the warning names keys
// only, never secret values.
func TestServerApplyReloadKeepsRestartOnlySettings(t *testing.T) {
	buf := &syncBuffer{}
	server := newReloadTestServer(t, buf)
	limiter := &fakeLiveLimiter{}
	server.rateLimiter = limiter

	next := Config{
		ListenAddr:      ":9090",
		EngineGRPCAddr:  "engine.other:50051",
		EngineAuthToken: "top-secret-token",
		LogLevel:        "debug",
		RateLimitRPS:    25,
		RateLimitBurst:  40,
	}
	server.applyReload(next)

	if server.cfg.ListenAddr != ":8080" {
		t.Fatalf("listen address = %q, want the running :8080", server.cfg.ListenAddr)
	}
	if server.cfg.EngineGRPCAddr != "engine:50051" {
		t.Fatalf("engine address = %q, want the running engine:50051", server.cfg.EngineGRPCAddr)
	}
	if server.cfg.EngineAuthToken != "" {
		t.Fatal("the shared secret must keep its running value")
	}
	if server.cfg.LogLevel != "debug" {
		t.Fatalf("log level = %q, want the reloaded debug", server.cfg.LogLevel)
	}
	rps, burst, calls := limiter.snapshot()
	if rps != 25 || burst != 40 || calls != 1 {
		t.Fatalf("limiter = %v/%d after %d calls, want the reloaded 25/40 applied once", rps, burst, calls)
	}
	logs := buf.String()
	for _, want := range []string{"listen_addr", "engine_grpc_addr", "engine_auth_token", "need a restart"} {
		if !strings.Contains(logs, want) {
			t.Fatalf("restart-only warning must contain %q:\n%s", want, logs)
		}
	}
	if strings.Contains(logs, "top-secret-token") {
		t.Fatalf("the warning must never log the secret value:\n%s", logs)
	}
}

// TestApplyReloadChangesLogLevel verifies a reload changes verbosity in
// place through the level handler the logger wraps.
func TestApplyReloadChangesLogLevel(t *testing.T) {
	inner := &syncBuffer{}
	level := NewLevelHandler(slog.NewTextHandler(inner, &slog.HandlerOptions{Level: slog.LevelDebug}), slog.LevelInfo)
	server := newReloadTestServer(t, &syncBuffer{})
	server.level = level
	logger := slog.New(level)

	logger.Debug("before reload")
	if strings.Contains(inner.String(), "before reload") {
		t.Fatal("the info level must filter debug records")
	}

	next := server.cfg
	next.LogLevel = "debug"
	server.applyReload(next)

	logger.Debug("after reload")
	if !strings.Contains(inner.String(), "after reload") {
		t.Fatalf("debug records must pass after the reload:\n%s", inner.String())
	}
}

// TestApplyReloadWarnsWhenLimiterNotMounted verifies enabling rate limiting
// live is refused loudly: the chain is built at startup, so a reload keeps
// the unmounted state and its running values, and says a restart is needed.
func TestApplyReloadWarnsWhenLimiterNotMounted(t *testing.T) {
	buf := &syncBuffer{}
	server := newReloadTestServer(t, buf)
	server.rateLimiter = nil

	next := server.cfg
	next.RateLimitRPS = 30
	next.RateLimitBurst = 60
	server.applyReload(next)

	if server.rateLimiter != nil {
		t.Fatal("a reload must not mount a limiter into the built chain")
	}
	if server.cfg.RateLimitRPS != 10 || server.cfg.RateLimitBurst != 20 {
		t.Fatalf("running rate limit = %v/%d, want the running 10/20", server.cfg.RateLimitRPS, server.cfg.RateLimitBurst)
	}
	if logs := buf.String(); !strings.Contains(logs, "enabling it needs a restart") {
		t.Fatalf("missing the mount warning:\n%s", logs)
	}
}

// TestWatchConfigWithoutFileDoesNothing verifies a server configured from
// the environment alone starts no watcher and records no level handler.
func TestWatchConfigWithoutFileDoesNothing(t *testing.T) {
	server := newReloadTestServer(t, &syncBuffer{})
	server.WatchConfig(context.Background(), nil)
	if server.level != nil {
		t.Fatal("no level handler may be recorded without a file to watch")
	}
}

// TestServerWatchConfigEndToEnd verifies the wiring the binary uses: a
// server built from a file configuration picks up a new version, logs the
// reload, and carries the new log level into the level handler. It runs
// watchConfig at a fast interval, the same path WatchConfig takes at the
// production one.
func TestServerWatchConfigEndToEnd(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	buf := &syncBuffer{}
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), testLogger(buf))
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	level := NewLevelHandler(slog.NewTextHandler(&syncBuffer{}, &slog.HandlerOptions{Level: slog.LevelDebug}), ParseLevel(cfg.LogLevel))

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	server.watchConfig(ctx, level, time.Millisecond)

	rewriteConfigFile(t, path, configWith("debug", 10, 20))
	waitFor(t, func() bool { return strings.Contains(buf.String(), "config reloaded") }, "the reload to be logged")
	if !strings.Contains(buf.String(), "log_level=debug") {
		t.Fatalf("the reload line must carry the new level:\n%s", buf.String())
	}
}

// newReloadTestServer builds a server with the values a reload test
// compares its running configuration against: a mounted limiter, one
// trusted key, and fixed listener and engine addresses.
func newReloadTestServer(t *testing.T, buf *syncBuffer) *Server {
	t.Helper()
	cfg := Config{
		ListenAddr:     ":8080",
		EngineGRPCAddr: "engine:50051",
		RateLimitRPS:   10,
		RateLimitBurst: 20,
		TrustedAPIKeys: map[string]string{"key-1": "org-1"},
		RequestTimeout: 5 * time.Second,
		MaxBodyBytes:   1 << 20,
	}
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), testLogger(buf))
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	return server
}

// fakeLiveLimiter records the limits a reload applied, so a test observes
// the watcher's effect without reaching into the plugin package.
type fakeLiveLimiter struct {
	mu    sync.Mutex
	rps   float64
	burst int
	calls int
}

// SetLimit records one applied update.
func (f *fakeLiveLimiter) SetLimit(rps float64, burst int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.rps, f.burst, f.calls = rps, burst, f.calls+1
}

// snapshot returns the last applied limits and how often they were applied.
func (f *fakeLiveLimiter) snapshot() (float64, int, int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.rps, f.burst, f.calls
}

// TestRestartOnlyNames verifies the reported keys: the reloadable subset
// never appears, restart-only differences do, in a stable order.
func TestRestartOnlyNames(t *testing.T) {
	running := Config{
		ListenAddr:     ":8080",
		EngineGRPCAddr: "engine:50051",
		LogLevel:       "info",
		RateLimitRPS:   10,
		RateLimitBurst: 20,
		TrustedAPIKeys: map[string]string{"key-1": "org-1"},
		Plugins:        []string{"header"},
	}
	next := running
	next.LogLevel = "debug"
	next.RateLimitRPS = 25
	next.RateLimitBurst = 40
	if changed := restartOnlyNames(running, next); len(changed) != 0 {
		t.Fatalf("reloadable changes reported as restart-only: %v", changed)
	}

	next.ListenAddr = ":9090"
	next.TrustedAPIKeys = map[string]string{"key-2": "org-2"}
	next.Plugins = nil
	want := []string{"listen_addr", "api_keys", "plugins"}
	if changed := restartOnlyNames(running, next); !slices.Equal(changed, want) {
		t.Fatalf("changed = %v, want %v", changed, want)
	}
}

// TestParseLevel verifies a configured name maps onto its slog level and
// unknown or empty names fall back to info, matching the LoadConfig
// default.
func TestParseLevel(t *testing.T) {
	cases := map[string]struct {
		name string
		want slog.Level
	}{
		"empty falls back to info":   {"", slog.LevelInfo},
		"unknown falls back to info": {"verbose", slog.LevelInfo},
		"info maps to info":          {"info", slog.LevelInfo},
		"debug maps to debug":        {"debug", slog.LevelDebug},
		"warn maps to warn":          {"warn", slog.LevelWarn},
		"error maps to error":        {"error", slog.LevelError},
		"case-sensitive on input":    {"WARN", slog.LevelInfo},
	}
	for label, tc := range cases {
		t.Run(label, func(t *testing.T) {
			if got := ParseLevel(tc.name); got != tc.want {
				t.Fatalf("ParseLevel(%q) = %v, want %v", tc.name, got, tc.want)
			}
		})
	}
}

// TestLevelHandlerFiltersUntilSetLevel verifies the wrapper is the only
// gate: an inner handler at the lowest level leaves verbosity to SetLevel,
// which changes it in place.
func TestLevelHandlerFiltersUntilSetLevel(t *testing.T) {
	inner := &syncBuffer{}
	handler := NewLevelHandler(slog.NewTextHandler(inner, &slog.HandlerOptions{Level: slog.LevelDebug}), slog.LevelInfo)
	logger := slog.New(handler)

	logger.Debug("quiet at info")
	if strings.Contains(inner.String(), "quiet at info") {
		t.Fatal("the info level must filter debug records")
	}
	logger.Info("visible at info")
	if !strings.Contains(inner.String(), "visible at info") {
		t.Fatalf("info records must pass at the info level:\n%s", inner.String())
	}

	handler.SetLevel(slog.LevelDebug)
	logger.Debug("loud at debug")
	if !strings.Contains(inner.String(), "loud at debug") {
		t.Fatalf("debug records must pass after SetLevel:\n%s", inner.String())
	}
}

// TestLevelHandlerDerivedHandlersShareLevel verifies handlers derived by
// WithAttrs and WithGroup follow the same swappable level.
func TestLevelHandlerDerivedHandlersShareLevel(t *testing.T) {
	handler := NewLevelHandler(slog.NewTextHandler(&syncBuffer{}, &slog.HandlerOptions{Level: slog.LevelDebug}), slog.LevelWarn)
	attrs := handler.WithAttrs([]slog.Attr{slog.String("k", "v")})
	group := handler.WithGroup("g")

	handler.SetLevel(slog.LevelDebug)
	if !attrs.Enabled(context.Background(), slog.LevelDebug) {
		t.Fatal("a handler derived by WithAttrs must follow SetLevel")
	}
	if !group.Enabled(context.Background(), slog.LevelDebug) {
		t.Fatal("a handler derived by WithGroup must follow SetLevel")
	}
}

// TestReloadRacesRequestHandling rewrites configuration versions while
// requests run the full middleware chain, so the reload races the state a
// live request reads: the log level the logging middleware consults and the
// limiter buckets the plugin chain checks. The race detector is the
// assertion that this shared state is guarded. The final rewrite is a
// distinctive version, and once the watcher stops the server must hold that
// version in full, with level and limit from the same version rather than a
// torn mix.
func TestReloadRacesRequestHandling(t *testing.T) {
	path := configPath(t)
	rewriteConfigFile(t, path, baseConfig)
	useConfigFile(t, path)

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	buf := &syncBuffer{}
	level := NewLevelHandler(slog.NewTextHandler(buf, &slog.HandlerOptions{Level: slog.LevelDebug}), ParseLevel(cfg.LogLevel))
	server, err := NewServer(cfg, &mockEngineClient{}, ToolExecutorFunc(func(ctx context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		return protocol.ToolResult{}, nil
	}), metrics.NewRegistry(), slog.New(level))
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	server.level = level

	applied := &appliedRecorder{}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	// watchConfig starts a goroutine the test cannot join, so build the same
	// watcher by hand and run it through startWatcher: the stop function
	// waits for the loop to exit, which is what makes the final read of the
	// running configuration safe rather than a race with a late reload.
	watcher := newConfigWatcher(path, time.Millisecond, server.logger, func(next Config) {
		server.applyReload(next)
		applied.record(next)
	})
	stop := startWatcher(t, ctx, watcher)

	var served, unexpected atomic.Int32
	stopRequests := make(chan struct{})
	// The stop function is registered as a cleanup as well as called at the
	// end of the test, so a failed waitFor cannot leave the request
	// goroutines spinning for the rest of the run.
	stopOnce := sync.Once{}
	stopAll := func() { stopOnce.Do(func() { close(stopRequests) }) }
	t.Cleanup(stopAll)
	var requests sync.WaitGroup
	for i := 0; i < 4; i++ {
		requests.Add(1)
		go func() {
			defer requests.Done()
			for {
				select {
				case <-stopRequests:
					return
				default:
				}
				req := httptest.NewRequest(http.MethodGet, "/approvals?state=pending", nil)
				req.Header.Set("X-Api-Key", "key-1")
				rec := httptest.NewRecorder()
				server.httpSrv.Handler.ServeHTTP(rec, req)
				served.Add(1)
				// A racing version can empty this key's bucket, so 429 is as
				// valid as 200; any other status would mean a reload leaked a
				// torn state into request handling.
				if rec.Code != http.StatusOK && rec.Code != http.StatusTooManyRequests {
					unexpected.Add(1)
				}
			}
		}()
	}

	// Every version differs in size from its neighbours and from the final
	// one, so each write is a detectable version on any filesystem.
	for i, version := range []string{configWith("warn", 5, 5), configWith("info", 30, 60)} {
		rewriteConfigFile(t, path, version)
		waitFor(t, func() bool { return applied.count() >= i+1 }, "a racing version to apply")
	}
	if server.level.Enabled(context.Background(), slog.LevelDebug) {
		t.Fatal("the racing versions must not enable debug before the final version applies")
	}

	rewriteConfigFile(t, path, configWith("debug", 7, 7))
	waitFor(t, func() bool {
		last, ok := applied.last()
		return ok && last.LogLevel == "debug"
	}, "the final version to apply")

	stopAll()
	requests.Wait()
	cancel()
	stop()

	if got := served.Load(); got == 0 {
		t.Fatal("requests must run while the reloads apply")
	}
	if got := unexpected.Load(); got != 0 {
		t.Fatalf("%d requests answered outside 200 and 429 during the reloads", got)
	}
	if got := applied.count(); got < 3 {
		t.Fatalf("applied %d configurations, want the two racing versions and the final one", got)
	}
	// The watcher has exited, so the running configuration is stable to
	// read: it must match the final version as one, never a mix.
	if server.cfg.LogLevel != "debug" || server.cfg.RateLimitRPS != 7 || server.cfg.RateLimitBurst != 7 {
		t.Fatalf("settled on log level %q and limit %v/%d, want the final version debug 7/7",
			server.cfg.LogLevel, server.cfg.RateLimitRPS, server.cfg.RateLimitBurst)
	}
	if !server.level.Enabled(context.Background(), slog.LevelDebug) {
		t.Fatal("the settled log level must pass debug records")
	}
}
