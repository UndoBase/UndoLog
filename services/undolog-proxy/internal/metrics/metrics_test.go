package metrics

import (
	"strings"
	"testing"
)

// TestRegistryCounterRendersLabels verifies counter output, label escaping,
// and deterministic ordering of label sets.
func TestRegistryCounterRendersLabels(t *testing.T) {
	reg := NewRegistry()
	c := reg.Counter("undolog_proxy_requests_total", "Requests by route and status", "route", "status")
	c.Add(1, "/mcp/tool_call", "200")
	c.Add(2, "/mcp/tool_call", "500")
	c.Add(1, `bad/"route"`, "200")

	out := reg.Render()

	for _, want := range []string{
		"# HELP undolog_proxy_requests_total Requests by route and status",
		"# TYPE undolog_proxy_requests_total counter",
		`undolog_proxy_requests_total{route="/mcp/tool_call",status="200"} 1`,
		`undolog_proxy_requests_total{route="/mcp/tool_call",status="500"} 2`,
		`undolog_proxy_requests_total{route="bad/\"route\"",status="200"} 1`,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("expected output to contain %q, got:\n%s", want, out)
		}
	}

	pos200 := strings.Index(out, `status="200"`)
	pos500 := strings.Index(out, `status="500"`)
	if pos200 < 0 || pos500 < 0 || pos200 > pos500 {
		t.Errorf("expected lexicographic label ordering, got positions 200=%d 500=%d", pos200, pos500)
	}
}

// TestRegistryGauge verifies a gauge reports its current value per label set.
func TestRegistryGauge(t *testing.T) {
	reg := NewRegistry()
	g := reg.Gauge("subscribers", "Active SSE subscribers", "org")
	g.Set(3, "org-1")
	g.Set(5, "org-2")
	g.Set(2, "org-1")

	out := reg.Render()
	if !strings.Contains(out, `subscribers{org="org-1"} 2`) {
		t.Errorf("expected updated org-1 value, got:\n%s", out)
	}
	if !strings.Contains(out, `subscribers{org="org-2"} 5`) {
		t.Errorf("expected org-2 value, got:\n%s", out)
	}
}

// TestRegistryHistogram verifies bucket, sum, and count lines are consistent
// with the observed values.
func TestRegistryHistogram(t *testing.T) {
	reg := NewRegistry()
	h := reg.Histogram("rpc_duration", "Engine RPC latency", []float64{0.005, 0.01}, "method")
	h.Observe(0.001, "Intercept")
	h.Observe(0.007, "Intercept")
	h.Observe(0.02, "Intercept")

	out := reg.Render()
	for _, want := range []string{
		"# TYPE rpc_duration histogram",
		`rpc_duration_bucket{method="Intercept",le="0.005"} 1`,
		`rpc_duration_bucket{method="Intercept",le="0.01"} 2`,
		`rpc_duration_bucket{method="Intercept",le="+Inf"} 3`,
		`rpc_duration_sum{method="Intercept"} 0.028`,
		`rpc_duration_count{method="Intercept"} 3`,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("expected output to contain %q, got:\n%s", want, out)
		}
	}
}

// TestRegistryDefaultsToStandardBuckets verifies a nil bucket list falls back
// to the shared default bucket set.
func TestRegistryDefaultsToStandardBuckets(t *testing.T) {
	reg := NewRegistry()
	h := reg.Histogram("latency", "Latency", nil, "route")
	if len(h.bounds) != len(DefaultHistogramBuckets) {
		t.Fatalf("expected default buckets, got %d bounds", len(h.bounds))
	}
}

// TestRegistryOmitsUnobservedMetrics verifies metrics without labels or values
// are not rendered, keeping the endpoint output small.
func TestRegistryOmitsUnobservedMetrics(t *testing.T) {
	reg := NewRegistry()
	reg.Counter("never_used", "Never incremented", "route")
	reg.Histogram("empty_histo", "Never observed", nil, "route")

	out := reg.Render()
	if out != "" {
		t.Errorf("expected empty render for unobserved metrics, got:\n%s", out)
	}
}

// TestRegistryUnlabeledHistogram verifies an unlabeled histogram renders
// bucket lines with only the le label and sum/count lines without an empty
// label set.
func TestRegistryUnlabeledHistogram(t *testing.T) {
	reg := NewRegistry()
	h := reg.Histogram("plain_latency", "Unlabeled latency", []float64{0.5, 1})
	h.Observe(0.2)
	h.Observe(0.8)

	out := reg.Render()
	for _, want := range []string{
		`plain_latency_bucket{le="0.5"} 1`,
		`plain_latency_bucket{le="1"} 2`,
		`plain_latency_bucket{le="+Inf"} 2`,
		"plain_latency_sum 1",
		"plain_latency_count 2",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("expected output to contain %q, got:\n%s", want, out)
		}
	}
	if strings.Contains(out, "plain_latency_sum{}") || strings.Contains(out, "plain_latency_count{}") {
		t.Errorf("expected no empty label set in output, got:\n%s", out)
	}
}

// TestRegistryConcurrentAllocations verifies registration and observation are
// safe under concurrent access with the race detector enabled.
func TestRegistryConcurrentAllocations(t *testing.T) {
	reg := NewRegistry()
	done := make(chan struct{})
	for i := 0; i < 8; i++ {
		go func() {
			defer func() { done <- struct{}{} }()
			for j := 0; j < 100; j++ {
				c := reg.Counter("requests", "Requests", "route")
				c.Add(1, "/mcp/tool_call")
				g := reg.Gauge("subs", "Subscribers", "org")
				g.Set(1, "org-1")
				h := reg.Histogram("dur", "Duration", nil, "route")
				h.Observe(0.001, "/mcp/tool_call")
				reg.Render()
			}
		}()
	}
	for range 8 {
		<-done
	}
}
