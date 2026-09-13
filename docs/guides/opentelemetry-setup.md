---
title: "OpenTelemetry Setup"
description: "Configure distributed tracing for UndoLog with OpenTelemetry and Grafana."
section: "guides"
---
# OpenTelemetry Setup

Configure distributed tracing for UndoLog with OpenTelemetry.

---

## Overview

UndoLog exports traces via OTLP/gRPC when configured. The engine creates
spans for each gRPC RPC (intercept, commit, fail, approve, reject) with
attributes including `session_id`, `step_index`, `tool_name`, `effect_id`,
and `outcome`. The proxy propagates W3C Trace Context headers from incoming
HTTP requests into outgoing gRPC metadata to the engine.

---

## Prerequisites

- An OTLP-compatible collector (e.g. Jaeger, Grafana Tempo, OpenTelemetry
  Collector)
- Grafana for dashboard visualization (optional)

---

## Configuration

### Engine

Set the OTLP endpoint to enable trace export:

```bash
# Point to your OTLP collector
UNDOLOG_OTEL_ENDPOINT=http://localhost:4317

# Optional: override service name (default: undolog-engine)
UNDOLOG_OTEL_SERVICE_NAME=undolog-engine

# Optional: override log level for the telemetry pipeline
UNDOLOG_OTEL_LOG_LEVEL=info
```

When `UNDOLOG_OTEL_ENDPOINT` is unset or empty, the engine falls back to
JSON log output only with no trace export.

### Proxy

The proxy automatically propagates W3C Trace Context from incoming HTTP
requests. No additional configuration is needed. The proxy reads the
`traceparent` header from `POST /mcp/tool_call` requests and injects it
into gRPC metadata sent to the engine.

---

## Span attributes

Each gRPC RPC creates a span with these attributes:

| Attribute | Description |
|-----------|-------------|
| `session_id` | UndoLog session identifier |
| `step_index` | Step number within the session |
| `tool_name` | Name of the tool being called |
| `org_id` | Organization identifier |
| `effect_id` | Effect identifier (set after intercept) |
| `rpc` | gRPC method name (e.g. `Intercept`, `Commit`) |

---

## Trace propagation

The proxy propagates trace context through two mechanisms:

1. **W3C Trace Context**: `traceparent` and `tracestate` headers from
   incoming HTTP requests are extracted and injected into gRPC metadata.
2. **X-Request-Id**: A custom request ID is propagated alongside trace
   context for log correlation.

---

## Running with Docker Compose

Add to your `docker-compose.yml`:

```yaml
services:
  jaeger:
    image: jaegertracing/all-in-one:1.62
    ports:
      - "16686:16686"  # UI
      - "4317:4317"    # OTLP gRPC
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

  engine:
    environment:
      UNDOLOG_OTEL_ENDPOINT: "http://jaeger:4317"
```

---

## Grafana dashboard

A pre-built Grafana dashboard is available at
`deploy/grafana/dashboard-undolog.json`. It includes trace-based panels
using Grafana Tempo. Import it into Grafana:

1. Navigate to Dashboards > Import
2. Upload the JSON file
3. Select your Tempo data source

Note: The Prometheus metrics panels in the dashboard are templates for
future instrumentation. They will populate once Prometheus metrics are
added to the engine.

---

## Verifying trace export

```bash
# Start the engine with OTLP enabled
UNDOLOG_OTEL_ENDPOINT=http://localhost:4317 cargo run -p undolog-engine

# Check Jaeger UI at http://localhost:16686
# Look for service "undolog-engine" with spans for each RPC
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UNDOLOG_OTEL_ENDPOINT` | (empty) | OTLP collector endpoint (e.g. `http://localhost:4317`) |
| `UNDOLOG_OTEL_SERVICE_NAME` | `undolog-engine` | Service name in trace resource attributes |
| `UNDOLOG_OTEL_LOG_LEVEL` | `info` | Log level for the telemetry pipeline |

---

## See also

- [Running in production](running-in-production.md): production deployment checklist
- [PostgreSQL high availability](postgresql-ha.md): WAL streaming, read replicas, failover
