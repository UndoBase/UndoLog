// Package metrics provides a small, dependency-free registry of counters,
// gauges, and histograms rendered in the Prometheus text exposition format.
//
// The registry is intentionally minimal: it covers the proxy observability
// surface (request counts and latency, engine RPC latency and errors, SSE
// subscriber and drop counts, approval decision latency, and upstream executor
// latency) without pulling in a Prometheus client library. Metrics are exposed
// unauthenticated on GET /metrics, alongside the GET /health liveness probe.
package metrics

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
)

// DefaultHistogramBuckets bounds the latency histograms used across the proxy.
var DefaultHistogramBuckets = []float64{0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

// Registry accumulates metrics under unique names and renders them on demand.
type Registry struct {
	mu       sync.RWMutex
	counters map[string]*Counter
	gauges   map[string]*Gauge
	histos   map[string]*Histogram
}

// NewRegistry creates an empty metric registry.
func NewRegistry() *Registry {
	return &Registry{
		counters: make(map[string]*Counter),
		gauges:   make(map[string]*Gauge),
		histos:   make(map[string]*Histogram),
	}
}

// Counter registers a counter with the given label names and returns it.
func (r *Registry) Counter(name, help string, labelNames ...string) *Counter {
	r.mu.Lock()
	defer r.mu.Unlock()
	if c, ok := r.counters[name]; ok {
		return c
	}
	c := &Counter{name: name, help: help, labelNames: labelNames, values: make(map[string]float64)}
	r.counters[name] = c
	return c
}

// Gauge registers a gauge with the given label names and returns it.
func (r *Registry) Gauge(name, help string, labelNames ...string) *Gauge {
	r.mu.Lock()
	defer r.mu.Unlock()
	if g, ok := r.gauges[name]; ok {
		return g
	}
	g := &Gauge{name: name, help: help, labelNames: labelNames, values: make(map[string]float64)}
	r.gauges[name] = g
	return g
}

// Histogram registers a histogram with the given label names and bucket bounds.
// Bucket bounds must be listed in non-decreasing order. When buckets is nil or
// empty, DefaultHistogramBuckets is used.
func (r *Registry) Histogram(name, help string, buckets []float64, labelNames ...string) *Histogram {
	r.mu.Lock()
	defer r.mu.Unlock()
	if h, ok := r.histos[name]; ok {
		return h
	}
	if len(buckets) == 0 {
		buckets = DefaultHistogramBuckets
	}
	h := &Histogram{
		name:       name,
		help:       help,
		labelNames: labelNames,
		bounds:     append([]float64(nil), buckets...),
		counts:     make(map[string][]uint64),
		sums:       make(map[string]float64),
	}
	r.histos[name] = h
	return h
}

// Render returns the registry contents in Prometheus text exposition format,
// with helpers, types, and series sorted by name for deterministic output.
// Only metrics with at least one observation are rendered.
func (r *Registry) Render() string {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var out strings.Builder

	names := make([]string, 0, len(r.counters))
	for name := range r.counters {
		if r.counters[name].HasObservations() {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	for _, name := range names {
		r.counters[name].render(&out)
	}

	names = names[:0]
	for name := range r.gauges {
		if r.gauges[name].HasObservations() {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	for _, name := range names {
		r.gauges[name].render(&out)
	}

	names = names[:0]
	for name := range r.histos {
		if r.histos[name].HasObservations() {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	for _, name := range names {
		r.histos[name].render(&out)
	}

	return out.String()
}

// labelKey joins the given label values into a map key for one label set.
func labelKey(labelValues []string) string {
	return strings.Join(labelValues, "\x00")
}

func writeLabels(buf *strings.Builder, labelNames, labelValues []string, extra, extraValue string) {
	buf.WriteByte('{')
	for i, name := range labelNames {
		if i > 0 {
			buf.WriteByte(',')
		}
		buf.WriteString(name)
		buf.WriteString(`="`)
		buf.WriteString(escapeLabelValue(labelValues[i]))
		buf.WriteByte('"')
	}
	if extra != "" {
		if len(labelNames) > 0 {
			buf.WriteByte(',')
		}
		buf.WriteString(extra)
		buf.WriteString(`="`)
		buf.WriteString(extraValue)
		buf.WriteByte('"')
	}
	buf.WriteByte('}')
}

func escapeLabelValue(v string) string {
	v = strings.ReplaceAll(v, `\`, `\\`)
	v = strings.ReplaceAll(v, "\n", `\n`)
	v = strings.ReplaceAll(v, `"`, `\"`)
	return v
}

func formatFloat(v float64) string {
	return strconv.FormatFloat(v, 'g', -1, 64)
}

func writeHead(buf *strings.Builder, name, help, typeName string) {
	buf.WriteString("# HELP ")
	buf.WriteString(name)
	buf.WriteString(" ")
	buf.WriteString(help)
	buf.WriteByte('\n')
	buf.WriteString("# TYPE ")
	buf.WriteString(name)
	buf.WriteString(" ")
	buf.WriteString(typeName)
	buf.WriteByte('\n')
}

// Counter is a monotonically increasing value distinguished by label set.
type Counter struct {
	name       string
	help       string
	labelNames []string
	observed   atomic.Bool
	mu         sync.RWMutex
	values     map[string]float64
}

// HasObservations reports whether Add was ever called.
func (c *Counter) HasObservations() bool { return c.observed.Load() }

// Add increments the counter for the given label set by delta.
// It may panic if the label value count does not match the registered labels.
func (c *Counter) Add(delta float64, labelValues ...string) {
	if err := checkLabels(len(c.labelNames), len(labelValues), c.name); err != nil {
		panic(err)
	}
	c.observed.Store(true)
	c.mu.Lock()
	key := labelKey(labelValues)
	c.values[key] += delta
	c.mu.Unlock()
}

func (c *Counter) render(out *strings.Builder) {
	writeHead(out, c.name, c.help, "counter")
	c.mu.RLock()
	keys := sortedKeys(c.values)
	for _, key := range keys {
		labelValues := strings.Split(key, "\x00")
		var line strings.Builder
		line.WriteString(c.name)
		if len(c.labelNames) > 0 {
			writeLabels(&line, c.labelNames, labelValues, "", "")
		}
		line.WriteString(" ")
		line.WriteString(formatFloat(c.values[key]))
		line.WriteByte('\n')
		out.WriteString(line.String())
	}
	c.mu.RUnlock()
}

// Gauge is a value that can rise and fall, distinguished by label set.
type Gauge struct {
	name       string
	help       string
	labelNames []string
	observed   atomic.Bool
	mu         sync.RWMutex
	values     map[string]float64
}

// HasObservations reports whether the gauge was ever set.
func (g *Gauge) HasObservations() bool { return g.observed.Load() }

// Set replaces the gauge value for the given label set.
func (g *Gauge) Set(value float64, labelValues ...string) {
	if err := checkLabels(len(g.labelNames), len(labelValues), g.name); err != nil {
		panic(err)
	}
	g.observed.Store(true)
	g.mu.Lock()
	g.values[labelKey(labelValues)] = value
	g.mu.Unlock()
}

func (g *Gauge) render(out *strings.Builder) {
	writeHead(out, g.name, g.help, "gauge")
	g.mu.RLock()
	for _, key := range sortedKeys(g.values) {
		labelValues := strings.Split(key, "\x00")
		var line strings.Builder
		line.WriteString(g.name)
		if len(g.labelNames) > 0 {
			writeLabels(&line, g.labelNames, labelValues, "", "")
		}
		line.WriteString(" ")
		line.WriteString(formatFloat(g.values[key]))
		line.WriteByte('\n')
		out.WriteString(line.String())
	}
	g.mu.RUnlock()
}

// Histogram accumulates observations into configurable buckets per label set,
// exposing _bucket, _sum, and _count series in the Prometheus format.
type Histogram struct {
	name       string
	help       string
	labelNames []string
	bounds     []float64
	observed   atomic.Bool

	mu     sync.RWMutex
	counts map[string][]uint64
	sums   map[string]float64
}

// HasObservations reports whether the histogram was ever observed.
func (h *Histogram) HasObservations() bool { return h.observed.Load() }

// Observe records one value for the given label set.
func (h *Histogram) Observe(value float64, labelValues ...string) {
	if err := checkLabels(len(h.labelNames), len(labelValues), h.name); err != nil {
		panic(err)
	}
	h.observed.Store(true)
	// bucket = the number of configured bounds that the value does not exceed,
	// so index 0 counts values <= bounds[0] and the last bucket counts every
	// observed value (always emitted as le="+Inf").
	bucket := 0
	for bucket < len(h.bounds) && !(value <= h.bounds[bucket]) {
		bucket++
	}
	h.mu.Lock()
	key := labelKey(labelValues)
	if _, ok := h.counts[key]; !ok {
		h.counts[key] = make([]uint64, len(h.bounds)+1)
	}
	h.counts[key][bucket]++
	h.sums[key] += value
	h.mu.Unlock()
}

func (h *Histogram) render(out *strings.Builder) {
	writeHead(out, h.name, h.help, "histogram")
	h.mu.RLock()
	for _, key := range sortedKeys(h.counts) {
		labelValues := strings.Split(key, "\x00")
		counts := h.counts[key]
		var total uint64
		for i := 0; i < len(h.bounds); i++ {
			total += counts[i]
			var line strings.Builder
			line.WriteString(h.name)
			line.WriteString("_bucket")
			writeLabels(&line, h.labelNames, labelValues, "le", formatFloat(h.bounds[i]))
			line.WriteString(" ")
			line.WriteString(strconv.FormatUint(total, 10))
			line.WriteByte('\n')
			out.WriteString(line.String())
		}
		total += counts[len(h.bounds)]
		var line strings.Builder
		line.WriteString(h.name)
		line.WriteString("_bucket")
		writeLabels(&line, h.labelNames, labelValues, "le", "+Inf")
		line.WriteString(" ")
		line.WriteString(strconv.FormatUint(total, 10))
		line.WriteByte('\n')
		out.WriteString(line.String())

		line.Reset()
		line.WriteString(h.name)
		line.WriteString("_sum")
		if len(h.labelNames) > 0 {
			writeLabels(&line, h.labelNames, labelValues, "", "")
		}
		line.WriteByte(' ')
		line.WriteString(formatFloat(h.sums[key]))
		line.WriteByte('\n')
		out.WriteString(line.String())

		line.Reset()
		line.WriteString(h.name)
		line.WriteString("_count")
		if len(h.labelNames) > 0 {
			writeLabels(&line, h.labelNames, labelValues, "", "")
		}
		line.WriteByte(' ')
		line.WriteString(strconv.FormatUint(total, 10))
		line.WriteByte('\n')
		out.WriteString(line.String())
	}
	h.mu.RUnlock()
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func checkLabels(want, got int, name string) error {
	if want != got {
		return fmt.Errorf("metric %s: got %d label values, want %d", name, got, want)
	}
	return nil
}
