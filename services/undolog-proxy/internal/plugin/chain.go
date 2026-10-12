package plugin

import (
	"log/slog"
	"net/http"
)

// RejectFunc writes the proxy's standard JSON error envelope for a plugin
// rejection. The proxy injects it so the chain keeps one error format
// without importing the proxy package.
type RejectFunc func(w http.ResponseWriter, r *http.Request, status int, reason string)

// maxHTTPStatus is the highest valid HTTP status code; the range above it
// is unassigned, so a rejection outside 400 to 599 is a plugin bug that the
// chain replaces with 403.
const maxHTTPStatus = 599

// Chain runs a fixed list of plugins around one handler. Before hooks run in
// the configured order, a rejection stops the chain with one log line, and
// After hooks run in reverse order when the handler first commits the
// response.
type Chain struct {
	plugins []Plugin
	logger  *slog.Logger
	reject  RejectFunc
}

// NewChain builds a chain for the given plugins. A nil logger defaults to
// the standard logger. reject must not be nil: it is how the chain writes
// the proxy's standard error envelope for a rejection.
func NewChain(plugins []Plugin, logger *slog.Logger, reject RejectFunc) *Chain {
	if logger == nil {
		logger = slog.Default()
	}
	return &Chain{plugins: plugins, logger: logger, reject: reject}
}

// Then runs the chain around next. With no plugins it returns next
// unchanged, so an empty configuration behaves exactly as if the chain did
// not exist.
func (c *Chain) Then(next http.Handler) http.Handler {
	if len(c.plugins) == 0 {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for _, p := range c.plugins {
			d := p.Before(r)
			if d.Reason == "" {
				continue
			}
			status := d.Status
			if status < http.StatusBadRequest || status > maxHTTPStatus {
				status = http.StatusForbidden
			}
			for name, value := range d.Headers {
				w.Header().Set(name, value)
			}
			c.logger.Warn("plugin rejected request",
				"request_id", r.Header.Get("X-Request-Id"),
				"plugin", p.Name(),
				"reason", d.Reason,
				"path", r.URL.Path,
			)
			c.reject(w, r, status, d.Reason)
			return
		}
		next.ServeHTTP(&commitWriter{ResponseWriter: w, request: r, after: reversed(c.plugins)}, r)
	})
}

// reversed returns the plugins in reverse chain order, the order in which
// After hooks run.
func reversed(plugins []Plugin) []Plugin {
	out := make([]Plugin, len(plugins))
	for i, p := range plugins {
		out[len(plugins)-1-i] = p
	}
	return out
}

// commitWriter runs After hooks exactly once, when the handler first commits
// the response, so every hook mutates headers before they reach the wire and
// the status line is written a single time. Writes, flushes, and unwrapping
// after the commit pass through untouched, which keeps Server-Sent Events
// streaming through the chain.
type commitWriter struct {
	http.ResponseWriter
	request   *http.Request
	after     []Plugin
	committed bool
}

// commit runs the After hooks in reverse chain order and then commits the
// status line. The committed flag makes later calls no-ops, so the first
// commit wins exactly as it does on the underlying writer.
func (w *commitWriter) commit(status int) {
	if w.committed {
		return
	}
	w.committed = true
	for _, p := range w.after {
		p.After(w, w.request)
	}
	w.ResponseWriter.WriteHeader(status)
}

// WriteHeader commits the response through the hook pass.
func (w *commitWriter) WriteHeader(status int) {
	w.commit(status)
}

// Write commits the response on the first payload write when the handler
// has not already done so, mirroring the implicit status of the standard
// library writer.
func (w *commitWriter) Write(b []byte) (int, error) {
	if !w.committed {
		w.commit(http.StatusOK)
	}
	return w.ResponseWriter.Write(b)
}

// Flush commits the response before flushing when a handler streams without
// writing first, so streamed headers carry the After hook values.
func (w *commitWriter) Flush() {
	if !w.committed {
		w.commit(http.StatusOK)
	}
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap exposes the underlying writer so http.ResponseController can reach
// connection-level controls, such as clearing the write deadline for SSE.
func (w *commitWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}
