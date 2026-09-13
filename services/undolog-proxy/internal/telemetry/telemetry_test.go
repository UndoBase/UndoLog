package telemetry

import (
	"net/http"
	"testing"
)

func TestParseTraceparent_Valid(t *testing.T) {
	header := "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
	sc, ok := ParseTraceparent(header)
	if !ok {
		t.Fatal("expected valid parse")
	}
	if sc.TraceID != [16]byte{0x0a, 0xf7, 0x65, 0x19, 0x16, 0xcd, 0x43, 0xdd, 0x84, 0x48, 0xeb, 0x21, 0x1c, 0x80, 0x31, 0x9c} {
		t.Errorf("trace ID mismatch: %x", sc.TraceID)
	}
	if sc.SpanID != [8]byte{0xb7, 0xad, 0x6b, 0x71, 0x69, 0x20, 0x33, 0x31} {
		t.Errorf("span ID mismatch: %x", sc.SpanID)
	}
	if !sc.IsSampled() {
		t.Error("expected sampled flag to be set")
	}
}

func TestParseTraceparent_NotSampled(t *testing.T) {
	header := "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-00"
	sc, ok := ParseTraceparent(header)
	if !ok {
		t.Fatal("expected valid parse")
	}
	if sc.IsSampled() {
		t.Error("expected sampled flag to be unset")
	}
}

func TestParseTraceparent_Invalid(t *testing.T) {
	cases := []string{
		"",
		"00",
		"00-abc",
		"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331",
		"01-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
		"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-0g",
	}
	for _, c := range cases {
		if _, ok := ParseTraceparent(c); ok {
			t.Errorf("expected invalid parse for %q", c)
		}
	}
}

func TestSpanContext_Traceparent(t *testing.T) {
	sc := SpanContext{
		TraceID: [16]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16},
		SpanID:  [8]byte{1, 2, 3, 4, 5, 6, 7, 8},
		Flags:   0x01,
	}
	tp := sc.Traceparent()
	if tp != "00-0102030405060708090a0b0c0d0e0f10-0102030405060708-01" {
		t.Errorf("unexpected traceparent: %s", tp)
	}
}

func TestExtractFromRequest_WithHeader(t *testing.T) {
	req, _ := http.NewRequest("GET", "/", nil)
	req.Header.Set(traceparentHeader, "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
	req.Header.Set(tracestateHeader, "vendor=value")

	sc := ExtractFromRequest(req)
	if sc.TraceID != [16]byte{0x0a, 0xf7, 0x65, 0x19, 0x16, 0xcd, 0x43, 0xdd, 0x84, 0x48, 0xeb, 0x21, 0x1c, 0x80, 0x31, 0x9c} {
		t.Errorf("trace ID mismatch: %x", sc.TraceID)
	}
	if sc.State != "vendor=value" {
		t.Errorf("trace state mismatch: %s", sc.State)
	}
}

func TestExtractFromRequest_NoHeader(t *testing.T) {
	req, _ := http.NewRequest("GET", "/", nil)
	sc := ExtractFromRequest(req)
	// Should generate a random context
	if sc.TraceID == [16]byte{} {
		t.Error("expected non-zero trace ID")
	}
	if sc.SpanID == [8]byte{} {
		t.Error("expected non-zero span ID")
	}
}

func TestPropagateMetadata(t *testing.T) {
	sc := SpanContext{
		TraceID: [16]byte{0x0a, 0xf7, 0x65, 0x19, 0x16, 0xcd, 0x43, 0xdd, 0x84, 0x48, 0xeb, 0x21, 0x1c, 0x80, 0x31, 0x9c},
		SpanID:  [8]byte{0xb7, 0xad, 0x6b, 0x71, 0x69, 0x20, 0x33, 0x31},
		Flags:   0x01,
		State:   "vendor=value",
	}
	tp, ts := PropagateMetadata(sc)
	if tp != "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01" {
		t.Errorf("unexpected traceparent: %s", tp)
	}
	if ts != "vendor=value" {
		t.Errorf("unexpected tracestate: %s", ts)
	}
}

func TestContextFromRequest(t *testing.T) {
	req, _ := http.NewRequest("GET", "/", nil)
	req.Header.Set(traceparentHeader, "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")

	ctx := ContextFromRequest(req)
	sc, ok := SpanContextFrom(ctx)
	if !ok {
		t.Fatal("expected span context in context")
	}
	if sc.TraceID != [16]byte{0x0a, 0xf7, 0x65, 0x19, 0x16, 0xcd, 0x43, 0xdd, 0x84, 0x48, 0xeb, 0x21, 0x1c, 0x80, 0x31, 0x9c} {
		t.Errorf("trace ID mismatch: %x", sc.TraceID)
	}
}

func TestInjectResponseHeaders(t *testing.T) {
	sc := SpanContext{
		TraceID: [16]byte{0x0a, 0xf7, 0x65, 0x19, 0x16, 0xcd, 0x43, 0xdd, 0x84, 0x48, 0xeb, 0x21, 0x1c, 0x80, 0x31, 0x9c},
		SpanID:  [8]byte{0xb7, 0xad, 0x6b, 0x71, 0x69, 0x20, 0x33, 0x31},
		Flags:   0x01,
		State:   "vendor=value",
	}
	w := &fakeResponseWriter{header: http.Header{}}
	InjectResponseHeaders(w, sc)

	if w.header.Get(traceparentHeader) != "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01" {
		t.Errorf("unexpected traceparent: %s", w.header.Get(traceparentHeader))
	}
	if w.header.Get(tracestateHeader) != "vendor=value" {
		t.Errorf("unexpected tracestate: %s", w.header.Get(tracestateHeader))
	}
}

// fakeResponseWriter is a minimal http.ResponseWriter for testing.
type fakeResponseWriter struct {
	header http.Header
}

func (f *fakeResponseWriter) Header() http.Header         { return f.header }
func (f *fakeResponseWriter) Write(b []byte) (int, error) { return len(b), nil }
func (f *fakeResponseWriter) WriteHeader(statusCode int)  {}
