package main

import (
	"log/slog"
	"testing"
)

func TestLogLevel(t *testing.T) {
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
			if got := logLevel(tc.name); got != tc.want {
				t.Fatalf("logLevel(%q) = %v, want %v", tc.name, got, tc.want)
			}
		})
	}
}
