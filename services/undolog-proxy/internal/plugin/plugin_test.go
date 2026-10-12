// Package plugin tests the interceptor chain: configured ordering,
// short-circuit rejections with one log line, request modification, response
// header timing on buffered and streaming responses, and the built-in
// registry that fails fast on unknown names.
package plugin

import (
	"bytes"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// stubPlugin records every hook call in order and returns a fixed decision.
// Optional callbacks exercise request modification and header observation.
type stubPlugin struct {
	name     string
	decision Decision
	events   *[]string
	onBefore func(*http.Request)
	onAfter  func(http.ResponseWriter)
}

// Name returns the stub's configured name.
func (s *stubPlugin) Name() string { return s.name }

// Before records the call, runs the optional modifier, and returns the
// configured decision.
func (s *stubPlugin) Before(r *http.Request) Decision {
	*s.events = append(*s.events, "before:"+s.name)
	if s.onBefore != nil {
		s.onBefore(r)
	}
	return s.decision
}

// After records the call and runs the optional observer.
func (s *stubPlugin) After(w http.ResponseWriter, _ *http.Request) {
	*s.events = append(*s.events, "after:"+s.name)
	if s.onAfter != nil {
		s.onAfter(w)
	}
}

// captureLogger returns a logger that writes warn-level lines into buf.
func captureLogger(buf *bytes.Buffer) *slog.Logger {
	return slog.New(slog.NewTextHandler(buf, &slog.HandlerOptions{Level: slog.LevelWarn}))
}

// unexpectedRejection fails the test if the chain rejects a request that
// should be allowed.
func unexpectedRejection(t *testing.T) RejectFunc {
	t.Helper()
	return func(http.ResponseWriter, *http.Request, int, string) {
		t.Error("unexpected rejection")
	}
}

// countingWriter counts committed status lines around a recorder, so a test
// can prove the chain writes the status exactly once.
type countingWriter struct {
	http.ResponseWriter
	commits int
}

// WriteHeader records one committed status line before forwarding it.
func (w *countingWriter) WriteHeader(status int) {
	w.commits++
	w.ResponseWriter.WriteHeader(status)
}

// TestChainRunsHooksInConfiguredOrder verifies Before hooks run in the
// configured order before the handler, and After hooks run in reverse order
// when the response commits.
func TestChainRunsHooksInConfiguredOrder(t *testing.T) {
	events := []string{}
	var observedByHandler []string
	chain := NewChain([]Plugin{
		&stubPlugin{name: "first", events: &events},
		&stubPlugin{name: "second", events: &events},
	}, nil, unexpectedRejection(t))
	handler := chain.Then(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		observedByHandler = append(observedByHandler, events...)
		w.WriteHeader(http.StatusOK)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/mcp/tool_call", nil))

	wantHandler := "before:first,before:second"
	if got := strings.Join(observedByHandler, ","); got != wantHandler {
		t.Fatalf("handler observed %q, want both Before hooks in configured order %q", got, wantHandler)
	}
	wantHooks := "before:first,before:second,after:second,after:first"
	if got := strings.Join(events, ","); got != wantHooks {
		t.Fatalf("hook order %q, want %q with After hooks reversed", got, wantHooks)
	}
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
}

// TestChainRejectionShortCircuits verifies a rejection stops later plugins
// and the handler, skips every After hook, answers once through the
// injected rejection writer, and emits exactly one log line naming the
// plugin and reason.
func TestChainRejectionShortCircuits(t *testing.T) {
	events := []string{}
	var buf bytes.Buffer
	got := struct {
		status int
		reason string
		calls  int
	}{}
	chain := NewChain([]Plugin{
		&stubPlugin{name: "first", events: &events},
		&stubPlugin{
			name:     "blocker",
			events:   &events,
			decision: Decision{Status: http.StatusTooManyRequests, Reason: "source not allowed"},
		},
		&stubPlugin{name: "third", events: &events},
	}, captureLogger(&buf), func(_ http.ResponseWriter, _ *http.Request, status int, reason string) {
		got.calls++
		got.status = status
		got.reason = reason
	})
	handlerRan := false
	handler := chain.Then(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		handlerRan = true
	}))

	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/mcp/tool_call", nil))

	if handlerRan {
		t.Fatal("handler must not run after a rejection")
	}
	if got.calls != 1 {
		t.Fatalf("rejection writer called %d times, want 1", got.calls)
	}
	if got.status != http.StatusTooManyRequests || got.reason != "source not allowed" {
		t.Fatalf("rejection = (%d, %q), want (429, %q)", got.status, got.reason, "source not allowed")
	}
	wantHooks := "before:first,before:blocker"
	if joined := strings.Join(events, ","); joined != wantHooks {
		t.Fatalf("hooks = %q, want %q with no third plugin and no After hooks", joined, wantHooks)
	}
	logs := strings.TrimSuffix(buf.String(), "\n")
	if logs != "" && strings.Count(logs, "\n") != 0 {
		t.Fatalf("rejection must log exactly one line, got %q", logs)
	}
	for _, want := range []string{"plugin rejected request", "plugin=blocker", "source not allowed"} {
		if !strings.Contains(logs, want) {
			t.Errorf("log line %q must contain %q", logs, want)
		}
	}
}

// TestChainRejectionStatusFallback verifies a rejection whose status is not
// an error code falls back to 403 instead of writing an invalid status
// line, while a valid error status is kept as the plugin chose.
func TestChainRejectionStatusFallback(t *testing.T) {
	cases := []struct {
		name   string
		status int
		want   int
	}{
		{name: "zero status", status: 0, want: http.StatusForbidden},
		{name: "success status", status: http.StatusOK, want: http.StatusForbidden},
		{name: "error status kept", status: http.StatusServiceUnavailable, want: http.StatusServiceUnavailable},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			events := []string{}
			var buf bytes.Buffer
			var got int
			chain := NewChain([]Plugin{
				&stubPlugin{
					name:     "blocker",
					events:   &events,
					decision: Decision{Status: tc.status, Reason: "blocked"},
				},
			}, captureLogger(&buf), func(_ http.ResponseWriter, _ *http.Request, status int, _ string) {
				got = status
			})
			handler := chain.Then(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Error("handler must not run after a rejection")
			}))

			handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))

			if got != tc.want {
				t.Fatalf("status = %d, want %d", got, tc.want)
			}
		})
	}
}

// TestChainPassesModifiedRequestToHandler verifies a Before hook's
// modification of the request is visible to the handler.
func TestChainPassesModifiedRequestToHandler(t *testing.T) {
	events := []string{}
	var seen string
	marker := &stubPlugin{name: "marker", events: &events}
	marker.onBefore = func(r *http.Request) {
		r.Header.Set("X-Marker", "set-by-plugin")
	}
	chain := NewChain([]Plugin{marker}, nil, unexpectedRejection(t))
	handler := chain.Then(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		seen = r.Header.Get("X-Marker")
	}))

	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/", nil))

	if seen != "set-by-plugin" {
		t.Fatalf("handler saw %q, want the plugin's modification", seen)
	}
}

// TestChainResponseHeadersCommitOnce verifies After hooks run in reverse
// chain order before the status line commits, the headers arrive with the
// handler's own status, and the status is written exactly once even if the
// handler calls WriteHeader twice.
func TestChainResponseHeadersCommitOnce(t *testing.T) {
	events := []string{}
	chain := NewChain([]Plugin{
		&stubPlugin{
			name:   "outer",
			events: &events,
			onAfter: func(w http.ResponseWriter) {
				w.Header().Set("X-Outer", "set")
			},
		},
		&stubPlugin{
			name:   "inner",
			events: &events,
			onAfter: func(w http.ResponseWriter) {
				w.Header().Set("X-Inner", "set")
			},
		},
	}, nil, unexpectedRejection(t))
	handler := chain.Then(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusCreated)
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("created"))
	}))

	counter := &countingWriter{ResponseWriter: httptest.NewRecorder()}
	handler.ServeHTTP(counter, httptest.NewRequest(http.MethodPost, "/mcp/tool_call", nil))
	rec, ok := counter.ResponseWriter.(*httptest.ResponseRecorder)
	if !ok {
		t.Fatalf("recorder type = %T", counter.ResponseWriter)
	}

	if counter.commits != 1 {
		t.Fatalf("status committed %d times, want exactly 1", counter.commits)
	}
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want the handler's 201", rec.Code)
	}
	for _, header := range []string{"X-Outer", "X-Inner"} {
		if rec.Header().Get(header) != "set" {
			t.Errorf("header %s = %q, want the After hook value", header, rec.Header().Get(header))
		}
	}
	wantHooks := "before:outer,before:inner,after:inner,after:outer"
	if got := strings.Join(events, ","); got != wantHooks {
		t.Fatalf("hook order %q, want %q with After hooks reversed", got, wantHooks)
	}
	if rec.Body.String() != "created" {
		t.Fatalf("body = %q, want %q", rec.Body.String(), "created")
	}
}

// TestChainSSEFlushKeepsHookHeaders verifies an After hook's header is
// committed before the first flush, so a streaming response carries it
// while the payload passes through untouched.
func TestChainSSEFlushKeepsHookHeaders(t *testing.T) {
	events := []string{}
	rec := httptest.NewRecorder()
	flushedAtHookTime := true
	chain := NewChain([]Plugin{
		&stubPlugin{
			name:   "stream",
			events: &events,
			onAfter: func(w http.ResponseWriter) {
				flushedAtHookTime = rec.Flushed
				w.Header().Set("X-Stream", "on")
			},
		},
	}, nil, unexpectedRejection(t))
	handler := chain.Then(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: ping\n\n"))
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
	}))

	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/events", nil))

	if flushedAtHookTime {
		t.Fatal("After hook must run before the first flush commits the response")
	}
	if !rec.Flushed {
		t.Fatal("handler flush must reach the underlying writer")
	}
	if rec.Header().Get("X-Stream") != "on" {
		t.Fatalf("streaming header = %q, want the After hook value", rec.Header().Get("X-Stream"))
	}
	if rec.Body.String() != "data: ping\n\n" {
		t.Fatalf("stream body = %q, want the payload untouched", rec.Body.String())
	}
}

// TestChainEmptyListPassesThrough verifies no plugins means no behaviour
// change: the handler answers exactly as it would without the chain and
// nothing is logged.
func TestChainEmptyListPassesThrough(t *testing.T) {
	var buf bytes.Buffer
	chain := NewChain(nil, captureLogger(&buf), unexpectedRejection(t))
	inner := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("X-Test", "kept")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte("payload"))
	})

	direct := httptest.NewRecorder()
	inner.ServeHTTP(direct, httptest.NewRequest(http.MethodGet, "/", nil))
	viaChain := httptest.NewRecorder()
	chain.Then(inner).ServeHTTP(viaChain, httptest.NewRequest(http.MethodGet, "/", nil))

	if viaChain.Code != direct.Code {
		t.Fatalf("status = %d through the chain, %d direct", viaChain.Code, direct.Code)
	}
	if viaChain.Body.String() != direct.Body.String() {
		t.Fatalf("body = %q through the chain, %q direct", viaChain.Body.String(), direct.Body.String())
	}
	if viaChain.Header().Get("X-Test") != direct.Header().Get("X-Test") {
		t.Fatalf("header = %q through the chain, %q direct", viaChain.Header().Get("X-Test"), direct.Header().Get("X-Test"))
	}
	if buf.Len() != 0 {
		t.Fatalf("empty chain logged %q, want silence", buf.String())
	}
}

// TestNewRejectsUnknownPluginName verifies a misconfigured list fails at
// construction with the offending name and the valid options, which stops
// the server at startup instead of surprising the first request.
func TestNewRejectsUnknownPluginName(t *testing.T) {
	_, err := New([]string{"header", "mystery"}, nil)
	if err == nil {
		t.Fatal("an unknown plugin name must fail")
	}
	for _, want := range []string{"mystery", "header", "UNDOLOG_PROXY_PLUGINS"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %v must contain %q", err, want)
		}
	}
}

// TestNewBuildsConfiguredPlugins verifies the configured names become
// plugins in order, and an empty list builds an empty chain without error.
func TestNewBuildsConfiguredPlugins(t *testing.T) {
	plugins, err := New([]string{"header"}, nil)
	if err != nil {
		t.Fatalf("new: %v", err)
	}
	if len(plugins) != 1 || plugins[0].Name() != "header" {
		t.Fatalf("plugins = %v, want exactly the header plugin", pluginNames(plugins))
	}
	empty, err := New(nil, nil)
	if err != nil || len(empty) != 0 {
		t.Fatalf("empty list: plugins %v, err %v, want an empty result", empty, err)
	}
}

// TestHeaderPluginSetsOutboundHeader verifies the built-in marks every
// proxied request with its header as seen by the handler.
func TestHeaderPluginSetsOutboundHeader(t *testing.T) {
	plugins, err := New([]string{"header"}, nil)
	if err != nil {
		t.Fatalf("new: %v", err)
	}
	var seen string
	handler := NewChain(plugins, nil, unexpectedRejection(t)).Then(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		seen = r.Header.Get("X-Custom-Header")
	}))

	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/mcp/tool_call", nil))

	if seen != "undolog" {
		t.Fatalf("outbound header = %q, want %q", seen, "undolog")
	}
}

// pluginNames returns the names of built plugins for failure messages.
func pluginNames(plugins []Plugin) []string {
	out := make([]string, 0, len(plugins))
	for _, p := range plugins {
		out = append(out, p.Name())
	}
	return out
}
