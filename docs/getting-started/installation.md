---
title: "Installation"
description: "This guide walks you through setting up UndoLog on your machine. You have two paths: the **Docker** path gets you running in about five minutes; the **manual** path gives you full control over each co"
section: "getting-started"
---
# Installation

This guide walks you through setting up UndoLog on your machine. You have two
paths: the **Docker** path gets you running in about five minutes; the
**manual** path gives you full control over each component.

---

## Prerequisites

Before you start, make sure you have these installed:

| Tool | Minimum version | Check with |
|---|---|---|
| Docker & Docker Compose | Docker 24+ | `docker --version` |
| Python | 3.10 | `python --version` |
| pip | 21+ | `pip --version` |
| PostgreSQL (manual only) | 16 | `psql --version` |
| Rust toolchain (manual only) | 1.75 | `rustc --version` |
| Go (manual only) | 1.22 | `go version` |

---

## Option 1: Try it in 5 minutes with Docker

Clone the repository and start the full stack:

```bash
git clone https://github.com/your-org/undolog.git
cd undolog
docker compose up -d
```

This launches four services defined in `infra/docker-compose.yml`:

- **postgres** (port 5432). PostgreSQL 16 with the schema applied automatically
- **engine** (port 50051). Rust effect engine, listens for gRPC requests
- **proxy** (port 8080). Go HTTP proxy, routes tool calls to the engine
- **dashboard** (port 3000). Next.js dashboard for monitoring sessions

After the stack starts, install the Python SDK in your application:

```bash
pip install undolog-sdk
```

You are done. Jump to [Verify it works](#verify-it-works).

---

## Option 2: Manual install

### 1. Set up PostgreSQL

Create the database and apply the migration:

```bash
createdb undolog
psql -d undolog -f migrations/0001_initial.sql
```

The migration creates all tables (`undolog_orgs`, `undolog_sessions`,
`undolog_effect_log`, `undolog_undo_stack`, `undolog_approval_requests`, etc.)
with row-level security, monthly-partitioned effect log, and the full
compensation registry.

### 2. Build and start the Rust engine

```bash
cargo build -p undolog-engine
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/undolog \
  ./target/debug/undolog-engine
```

The engine starts a gRPC server on `:50051`. It owns the effect-journal logic,
exactly-once deduplication, and compensation orchestration.

### 3. Build and start the Go proxy

```bash
cd services/undolog-proxy
go build -o undolog-proxy .
ENGINE_GRPC_ADDR=localhost:50051 \
  ./undolog-proxy
```

The proxy starts an HTTP server on `:8080`. It exposes the `/mcp/tool_call`
endpoint that the Python SDK talks to, with commit and fail handled inline
during the tool call lifecycle.

### 4. Install the Python SDK

```bash
pip install undolog-sdk
```

---

## Environment variables

UndoLog components read configuration from the environment. The most important
variables are:

| Variable | Default | Used by | Description |
|---|---|---|---|
| `UNDOLOG_PROXY_URL` | `http://localhost:8080` | Python SDK | Base URL of the Go proxy |
| `DATABASE_URL` |, | Engine, Proxy | PostgreSQL connection string |
| `ENGINE_GRPC_ADDR` | `localhost:50051` | Go Proxy | gRPC address of the Rust engine |
| `UNDOLOG_LOG_LEVEL` | `info` | All | Log verbosity (`debug`, `info`, `warn`, `error`) |
| `UNDOLOG_TOOL_TIMEOUT_SECS` | `300` | Engine | Max seconds per tool execution |
| `UNDOLOG_COMPENSATION_MAX_RETRIES` | `3` | Engine | Retry attempts for compensation calls |
| `UNDOLOG_COMPENSATION_RETRY_DELAY_MS` | `1000` | Engine | Base backoff delay between retries |

---

## Verify it works

Once the proxy is running, check its health endpoint:

```bash
curl http://localhost:8080/health
```

A healthy proxy returns:

```json
{"status":"ok","service":"undolog-proxy"}
```

Then run a quick smoke test from Python:

```python
import asyncio
from undolog_sdk import UndoLogClient, ToolTier

async def smoke_test():
    client = UndoLogClient()
    print(f"Proxy URL: {client._base_url}")
    print("SDK is ready.")

asyncio.run(smoke_test())
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl` returns `connection refused` | Proxy not started | Run `docker compose ps` or check the proxy logs |
| `UNDOLOG_PROXY_URL` not picked up | Env var not set | Export it: `export UNDOLOG_PROXY_URL=http://localhost:8080` |
| Engine logs `could not connect to database` | PostgreSQL not running | Start PostgreSQL or check `DATABASE_URL` |
| Proxy logs `no healthy upstream` | Engine not reachable | Verify the engine is running on `:50051` |
| SDK raises `httpx.RequestError` | Network or proxy down | Confirm the proxy is listening on port 8080 |
| Dashboard shows 0 sessions | No agent has connected | Run the quickstart agent and it appears automatically |

---

## Next steps

Now that UndoLog is running, protect your first agent in the
[Quickstart](quickstart.md) tutorial. When you are ready for a deeper look,
read the [Core concepts](concepts.md) guide.
