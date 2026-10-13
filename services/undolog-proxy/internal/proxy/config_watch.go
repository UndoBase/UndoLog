package proxy

import (
	"context"
	"log/slog"
	"os"
	"strings"
	"sync/atomic"
	"time"
)

// configWatchInterval bounds how often the proxy stats the configuration
// file. Polling keeps the standard library rule: a file watcher dependency
// is not worth a few seconds of delay on settings an operator changes
// rarely.
const configWatchInterval = 5 * time.Second

// ParseLevel maps a configured log-level name onto its slog.Level. Unknown
// or empty names fall back to info, matching the LoadConfig default.
func ParseLevel(name string) slog.Level {
	switch name {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// levelState holds the swappable verbosity shared by a LevelHandler and
// every handler it derives through WithAttrs or WithGroup.
type levelState struct {
	level atomic.Int64
}

// LevelHandler wraps a slog.Handler and filters records below a level that
// can change at runtime. The inner handler must be built at the lowest
// level so this wrapper is the only gate; a configuration reload then
// changes verbosity without rebuilding the logger every component already
// holds.
type LevelHandler struct {
	next  slog.Handler
	state *levelState
}

// NewLevelHandler wraps next with a runtime-changeable level. next must be
// built with its minimum level enabled, such as
// slog.NewJSONHandler(w, &slog.HandlerOptions{Level: slog.LevelDebug}).
func NewLevelHandler(next slog.Handler, level slog.Level) *LevelHandler {
	state := &levelState{}
	state.level.Store(int64(level))
	return &LevelHandler{next: next, state: state}
}

// SetLevel changes the minimum level for this handler and every handler
// derived from it, which is how a configuration reload changes verbosity.
func (h *LevelHandler) SetLevel(level slog.Level) {
	h.state.level.Store(int64(level))
}

// Enabled reports whether a record passes the current level.
func (h *LevelHandler) Enabled(_ context.Context, level slog.Level) bool {
	return level >= slog.Level(h.state.level.Load())
}

// Handle forwards the record to the wrapped handler.
func (h *LevelHandler) Handle(ctx context.Context, record slog.Record) error {
	return h.next.Handle(ctx, record)
}

// WithAttrs derives a handler that writes attrs with every record. The
// derived handler shares the swappable level with its parent.
func (h *LevelHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return &LevelHandler{next: h.next.WithAttrs(attrs), state: h.state}
}

// WithGroup derives a handler that groups attrs. The derived handler shares
// the swappable level with its parent.
func (h *LevelHandler) WithGroup(name string) slog.Handler {
	return &LevelHandler{next: h.next.WithGroup(name), state: h.state}
}

// fileStamp identifies one version of the configuration file by modification
// time and size, the pair a Kubernetes ConfigMap update changes when the
// mount's symlink switches to a new versioned directory.
type fileStamp struct {
	mod  time.Time
	size int64
}

// liveLimiter is the live-update surface of the mounted rate limiter, so
// the server can apply a reloaded limit without importing the plugin
// package's concrete type. The server's handle is nil when the proxy
// started without rate limiting.
type liveLimiter interface {
	SetLimit(rps float64, burst int)
}

// configWatcher polls one configuration file and applies each new version
// through reload. A version that fails to load or validate is rejected as a
// whole: reload never runs for it, so the last good configuration keeps
// serving until the next change.
type configWatcher struct {
	path     string
	interval time.Duration
	logger   *slog.Logger
	reload   func(next Config)
	// stamp is the file version last observed: the seed newConfigWatcher
	// takes, then the running cursor as versions apply.
	stamp fileStamp
}

// newConfigWatcher builds a watcher for path and seeds its comparison with
// the file's current stamp, so a change made before the first poll still
// counts as one. A non-positive interval falls back to the production
// default, and a nil logger falls back to the standard one, so a
// misconfigured watcher always runs.
func newConfigWatcher(path string, interval time.Duration, logger *slog.Logger, reload func(Config)) *configWatcher {
	if logger == nil {
		logger = slog.Default()
	}
	if interval <= 0 {
		interval = configWatchInterval
	}
	w := &configWatcher{path: path, interval: interval, logger: logger, reload: reload}
	if stamp, err := stampFile(path); err != nil {
		w.logger.Warn("config file is not readable yet; the last good configuration keeps serving", "path", path, "error", err)
	} else {
		w.stamp = stamp
	}
	return w
}

// run polls the file's stamp until ctx is canceled. A file that is briefly
// unreadable during an atomic swap is logged once per failure and the
// polling continues.
func (w *configWatcher) run(ctx context.Context) {
	ticker := time.NewTicker(w.interval)
	defer ticker.Stop()
	var lastErr string
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			next, err := stampFile(w.path)
			if err != nil {
				if err.Error() != lastErr {
					w.logger.Warn("config file unreadable; keeping the last good configuration", "path", w.path, "error", err)
					lastErr = err.Error()
				}
				continue
			}
			lastErr = ""
			if next == w.stamp {
				continue
			}
			w.stamp = next
			cfg, err := LoadConfig()
			if err != nil {
				w.logger.Error("config reload failed; keeping the last good configuration", "path", w.path, "error", err)
				continue
			}
			w.reload(cfg)
			w.logger.Info("config reloaded", "path", w.path, "log_level", cfg.LogLevel, "rate_limit_rps", cfg.RateLimitRPS, "rate_limit_burst", cfg.RateLimitBurst)
		}
	}
}

// stampFile returns the modification time and size that identify one
// version of the file. It follows symlinks, which is what makes a
// Kubernetes ConfigMap mount, a symlink to a versioned directory, visible
// as a change.
func stampFile(path string) (fileStamp, error) {
	info, err := os.Stat(path)
	if err != nil {
		return fileStamp{}, err
	}
	return fileStamp{mod: info.ModTime(), size: info.Size()}, nil
}

// WatchConfig polls the configuration file this server was loaded from and
// applies each new version's reloadable subset: the log level through
// level, which must be the LevelHandler the server's logger wraps, and the
// per-key rate limit through the mounted limiter. A nil level skips log
// level updates while rate limit reloads continue. A server loaded without
// a configuration file has nothing to watch, so the call does nothing.
func (s *Server) WatchConfig(ctx context.Context, level *LevelHandler) {
	s.watchConfig(ctx, level, configWatchInterval)
}

// watchConfig starts the poll loop at interval; WatchConfig runs it at the
// production interval, and tests run the same path faster.
func (s *Server) watchConfig(ctx context.Context, level *LevelHandler, interval time.Duration) {
	if s.cfg.filePath == "" {
		return
	}
	s.level = level
	watcher := newConfigWatcher(s.cfg.filePath, interval, s.logger, s.applyReload)
	go watcher.run(ctx)
}

// applyReload applies the reloadable subset of a freshly loaded
// configuration: the log level always, and the rate limit when the limiter
// is mounted. Settings that shape the already built server are named in one
// warning per version and keep their running values, and so do the rate
// limit values when no limiter is mounted to receive them.
func (s *Server) applyReload(next Config) {
	if changed := restartOnlyNames(s.cfg, next); len(changed) > 0 {
		s.logger.Warn("config reload: settings need a restart and keep their running values", "settings", strings.Join(changed, ", "))
	}
	s.cfg.LogLevel = next.LogLevel
	if s.level != nil {
		s.level.SetLevel(ParseLevel(next.LogLevel))
	}
	switch {
	case s.rateLimiter == nil && next.RateLimitRPS > 0:
		s.logger.Warn("config reload: rate limiting is not mounted; enabling it needs a restart")
	case s.rateLimiter != nil:
		s.rateLimiter.SetLimit(next.RateLimitRPS, next.RateLimitBurst)
		s.cfg.RateLimitRPS = next.RateLimitRPS
		s.cfg.RateLimitBurst = next.RateLimitBurst
	}
}
