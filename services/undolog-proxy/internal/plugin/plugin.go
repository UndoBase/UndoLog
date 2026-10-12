// Package plugin provides the proxy's configurable request interceptor chain.
//
// A deployment composes request policy, such as header rules or source
// blocks, from named plugins ordered by configuration. The chain always
// mounts after authentication, so Before hooks see the resolved organization,
// and it never replaces the proxy's process guarantees: panic recovery,
// request IDs, and structured logging stay hard-coded middlewares that wrap
// the chain.
package plugin

import (
	"fmt"
	"log/slog"
	"net/http"
	"slices"
	"strings"
)

// Decision is a plugin's verdict for one request. The zero value allows the
// request to continue unchanged.
type Decision struct {
	// Status is the HTTP status code sent for a rejection. Values outside
	// the 400 to 599 range are replaced with 403 by the chain, so a
	// malformed decision cannot write an invalid status line.
	Status int
	// Reason is the short explanation returned to the caller and written to
	// the rejection log line. An empty Reason allows the request.
	Reason string
	// Headers are response headers the chain writes with a rejection before
	// the error envelope, such as Retry-After on a rate-limit rejection.
	Headers map[string]string
}

// Plugin is one interceptor in the proxy's request chain.
//
// Hooks run inline on the request goroutine and must be quick and
// non-blocking. A panic in a hook is converted to the standard error
// response by the outermost panic recovery middleware.
type Plugin interface {
	// Name returns the plugin's configuration name as it appears in
	// UNDOLOG_PROXY_PLUGINS.
	Name() string

	// Before runs before the handler. It may modify r in place, or return
	// a rejection with a Status, Reason, and optional Headers, which stops
	// later plugins and the handler.
	Before(r *http.Request) Decision

	// After runs when the handler first commits the response and may set
	// response headers only. It runs in reverse chain order before the
	// status line reaches the wire, and never runs for a rejected request.
	After(w http.ResponseWriter, r *http.Request)
}

// builtins maps each configurable plugin name to its constructor. A new
// plugin registers here and is then selectable through UNDOLOG_PROXY_PLUGINS
// without touching the chain.
func builtins() map[string]func(*slog.Logger) Plugin {
	return map[string]func(*slog.Logger) Plugin{
		"header": func(*slog.Logger) Plugin { return headerPlugin{} },
	}
}

// names returns the valid plugin names in sorted order so configuration
// errors list the same options every time.
func names() []string {
	registry := builtins()
	out := make([]string, 0, len(registry))
	for name := range registry {
		out = append(out, name)
	}
	slices.Sort(out)
	return out
}

// New constructs the named plugins in order and injects the proxy's logger
// so plugins log without globals. An unknown name fails with an error naming
// the valid options: a misconfigured UNDOLOG_PROXY_PLUGINS stops the server
// at startup instead of surprising the first request.
func New(list []string, logger *slog.Logger) ([]Plugin, error) {
	if logger == nil {
		logger = slog.Default()
	}
	registry := builtins()
	out := make([]Plugin, 0, len(list))
	for _, name := range list {
		ctor, ok := registry[name]
		if !ok {
			return nil, fmt.Errorf("unknown plugin %q in UNDOLOG_PROXY_PLUGINS; valid plugins: %s", name, strings.Join(names(), ", "))
		}
		out = append(out, ctor(logger))
	}
	return out, nil
}
