// Package main starts the undolog-proxy HTTP service.
//
// It loads proxy configuration, builds the interception server, and exits
// cleanly on termination signals. The engine connection is established lazily
// by the first RPC and reconnects automatically, so the engine does not need to
// be running when the proxy starts.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"undolog-proxy/internal/engine"
	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/proxy"
)

func main() {
	if err := run(); err != nil {
		slog.Error("fatal", "error", err)
		os.Exit(1)
	}
}

// logLevel maps a configured log-level name onto its slog.Level. Unknown or
// empty names fall back to info, matching the LoadConfig default.
func logLevel(name string) slog.Level {
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

func run() error {
	cfg, err := proxy.LoadConfig()
	if err != nil {
		return err
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: logLevel(cfg.LogLevel)}))

	registry := metrics.NewRegistry()

	engineClient := engine.NewClient(cfg.EngineGRPCAddr, engine.RetryConfig{
		MaxAttempts: cfg.EngineRetryMaxAttempts,
		Backoff:     cfg.EngineRetryBackoff,
	}, logger)
	engineClient.SetMetrics(registry)
	defer func() { _ = engineClient.Close() }()

	toolExecutor, err := proxy.NewHTTPToolExecutor(cfg.UpstreamToolURL, cfg.RequestTimeout)
	if err != nil {
		return err
	}

	srv, err := proxy.NewServer(cfg, engineClient, toolExecutor, registry, logger)
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	return srv.Start(ctx)
}
