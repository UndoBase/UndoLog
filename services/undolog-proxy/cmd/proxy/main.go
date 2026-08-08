// Package main starts the undolog-proxy HTTP service.
//
// It loads proxy configuration, connects to the Rust engine, builds the
// interception server, and exits cleanly on termination signals.
package main

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"undolog-proxy/internal/engine"
	"undolog-proxy/internal/proxy"
)

func main() {
	if err := run(); err != nil {
		slog.Error("fatal", "error", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := proxy.LoadConfig()
	if err != nil {
		return err
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	if cfg.LogLevel == "debug" {
		logger = slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelDebug}))
	}

	engineClient := engine.NewClient(cfg.EngineGRPCAddr, engine.RetryConfig{
		MaxAttempts: cfg.EngineRetryMaxAttempts,
		Backoff:     cfg.EngineRetryBackoff,
	}, logger)
	defer func() { _ = engineClient.Close() }()

	connectCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := engineClient.Connect(connectCtx); err != nil {
		return err
	}

	transport := engine.NewGRPCTransport(engineClient.Conn())
	engineClient.SetTransport(transport)

	toolExecutor, err := proxy.NewHTTPToolExecutor(cfg.UpstreamToolURL, cfg.RequestTimeout)
	if err != nil {
		return err
	}

	srv, err := proxy.NewServer(cfg, engineClient, toolExecutor, logger)
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	return srv.Start(ctx)
}
