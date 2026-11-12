// Package main starts the undolog-proxy HTTP service.
//
// It loads proxy configuration, builds the interception server, and exits
// cleanly on termination signals. The engine connection is established lazily
// by the first RPC and reconnects automatically, so the engine does not need to
// be running when the proxy starts.
package main

import (
	"context"
	"fmt"
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

func run() error {
	cfg, err := proxy.LoadConfig()
	if err != nil {
		return err
	}

	// The JSON handler sits at the lowest level so LevelHandler is the only
	// gate; a configuration reload then changes verbosity in place instead
	// of rebuilding the logger every component holds.
	level := proxy.NewLevelHandler(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelDebug}), proxy.ParseLevel(cfg.LogLevel))
	logger := slog.New(level)

	registry := metrics.NewRegistry()

	var engineOpts []engine.Option
	if cfg.EngineAuthToken != "" {
		engineOpts = append(engineOpts, engine.WithAuthToken(cfg.EngineAuthToken))
	}
	engineOpts = append(engineOpts, engine.WithBackpressure(engine.BackpressureConfig{
		MaxInFlight:      cfg.MaxEngineRPCs,
		QueueMax:         cfg.EngineQueueMax,
		QueueWait:        cfg.EngineQueueWait,
		BreakerThreshold: cfg.EngineBreakerThreshold,
		BreakerReset:     cfg.EngineBreakerReset,
	}))
	if cfg.EngineTLSCAPath != "" || cfg.EngineTLSClientCertPath != "" || cfg.EngineTLSClientKeyPath != "" {
		creds, err := engine.BuildTransportCredentials(engine.TLSConfig{
			CAPath:         cfg.EngineTLSCAPath,
			ClientCertPath: cfg.EngineTLSClientCertPath,
			ClientKeyPath:  cfg.EngineTLSClientKeyPath,
		})
		if err != nil {
			return fmt.Errorf("engine TLS credentials: %w", err)
		}
		engineOpts = append(engineOpts, engine.WithTransportCredentials(creds))
	}

	engineClient := engine.NewClient(cfg.EngineGRPCAddr, engine.RetryConfig{
		MaxAttempts: cfg.EngineRetryMaxAttempts,
		Backoff:     cfg.EngineRetryBackoff,
	}, logger, engineOpts...)
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

	srv.WatchConfig(ctx, level)
	return srv.Start(ctx)
}
