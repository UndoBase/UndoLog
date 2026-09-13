// Package telemetry provides distributed tracing support for the UndoLog proxy.
//
// It extracts W3C Trace Context headers from incoming HTTP requests and
// propagates them into outgoing gRPC metadata to the engine, enabling
// end-to-end trace correlation across the proxy and engine services.
//
// The proxy does not export traces directly. Trace export is handled by
// the Rust engine via OTLP when UNDOLOG_OTEL_ENDPOINT is configured.
package telemetry

import (
	"context"
	"encoding/hex"
	"math/rand"
	"net/http"
	"strings"
)

// traceparentHeader is the W3C Trace Context header name.
const traceparentHeader = "traceparent"

// tracestateHeader is the W3C Trace State header name.
const tracestateHeader = "tracestate"

// contextKey is the unexported type for trace context values in context.Context.
type contextKey struct{}

// SpanContext carries the parsed W3C trace context for a request.
type SpanContext struct {
	TraceID [16]byte
	SpanID  [8]byte
	Flags   byte
	State   string
}

// IsSampled reports whether the sampled flag is set.
func (sc SpanContext) IsSampled() bool {
	return sc.Flags&0x01 != 0
}

// Traceparent returns the W3C traceparent header value.
func (sc SpanContext) Traceparent() string {
	version := "00"
	traceID := hex.EncodeToString(sc.TraceID[:])
	spanID := hex.EncodeToString(sc.SpanID[:])
	flags := "00"
	if sc.IsSampled() {
		flags = "01"
	}
	return version + "-" + traceID + "-" + spanID + "-" + flags
}

// WithSpanContext attaches a SpanContext to the context.
func WithSpanContext(ctx context.Context, sc SpanContext) context.Context {
	return context.WithValue(ctx, contextKey{}, sc)
}

// SpanContextFrom extracts the SpanContext from the context.
// Returns zero value and false if no context is present.
func SpanContextFrom(ctx context.Context) (SpanContext, bool) {
	sc, ok := ctx.Value(contextKey{}).(SpanContext)
	return sc, ok
}

// ExtractFromRequest parses the W3C traceparent header from an HTTP request
// and returns a SpanContext. If the header is missing or malformed, a new
// random trace context is generated so every request always has a trace ID.
func ExtractFromRequest(r *http.Request) SpanContext {
	tp := r.Header.Get(traceparentHeader)
	if sc, ok := ParseTraceparent(tp); ok {
		sc.State = r.Header.Get(tracestateHeader)
		return sc
	}
	return NewRandomSpanContext()
}

// ParseTraceparent parses a W3C traceparent header value.
// Format: version-traceID-parentID-traceFlags
// Returns false if the header is missing or invalid.
func ParseTraceparent(header string) (SpanContext, bool) {
	parts := strings.Split(header, "-")
	if len(parts) != 4 {
		return SpanContext{}, false
	}

	version, err := hex.DecodeString(parts[0])
	if err != nil || len(version) != 1 || version[0] != 0 {
		return SpanContext{}, false
	}

	traceID, err := hex.DecodeString(parts[1])
	if err != nil || len(traceID) != 16 {
		return SpanContext{}, false
	}

	spanID, err := hex.DecodeString(parts[2])
	if err != nil || len(spanID) != 8 {
		return SpanContext{}, false
	}

	flags, err := hex.DecodeString(parts[3])
	if err != nil || len(flags) != 1 {
		return SpanContext{}, false
	}

	var sc SpanContext
	copy(sc.TraceID[:], traceID)
	copy(sc.SpanID[:], spanID)
	sc.Flags = flags[0]
	return sc, true
}

// NewRandomSpanContext generates a new random trace context.
func NewRandomSpanContext() SpanContext {
	var sc SpanContext
	for i := range sc.TraceID {
		sc.TraceID[i] = byte(rand.Intn(256))
	}
	for i := range sc.SpanID {
		sc.SpanID[i] = byte(rand.Intn(256))
	}
	sc.Flags = 0x01 // sampled
	return sc
}

// PropagateMetadata returns gRPC metadata key-value pairs for the given
// SpanContext. The returned slice can be passed to metadata.AppendToOutgoingContext.
func PropagateMetadata(sc SpanContext) (traceparent, tracestate string) {
	return sc.Traceparent(), sc.State
}

// ContextFromRequest extracts the span context from the request, attaches
// it to the returned context, and adds it to the response writer as a
// server trace header.
func ContextFromRequest(r *http.Request) context.Context {
	sc := ExtractFromRequest(r)
	return WithSpanContext(r.Context(), sc)
}

// InjectResponseHeaders writes the trace context back to the HTTP response
// so clients can correlate their traces with the proxy.
func InjectResponseHeaders(w http.ResponseWriter, sc SpanContext) {
	w.Header().Set(traceparentHeader, sc.Traceparent())
	if sc.State != "" {
		w.Header().Set(tracestateHeader, sc.State)
	}
}
