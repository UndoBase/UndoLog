---
title: "How to deploy UndoLog with Docker"
description: "## Prerequisites"
section: "guides"
---
# How to deploy UndoLog with Docker

## Prerequisites

- Docker Engine >= 24.0 and Docker Compose >= 2.24
- The UndoLog repository cloned locally
- Ports 5432, 8080, 3000, 50051, and 9090 available

## What you'll build

You will deploy the full UndoLog stack. PostgreSQL 16, the Rust engine, the Go
proxy, and the Next.js dashboard: using Docker Compose. You will verify each
service with health checks and inspect the running deployment.

## Steps

### 1. Examine the Compose file

The stack is defined in `docker-compose.yml` at the repository root:

| Service | Image / Build | Ports | Base image |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `5432` | Alpine Linux |
| `engine` | `infra/Dockerfile.engine` | `50051` (gRPC), `9090` (health) | Distroless |
| `proxy` | `infra/Dockerfile.proxy` | `8080` | Scratch |
| `dashboard` | `infra/Dockerfile.dashboard` | `3000` | Node 22 Alpine |

### 2. Configure environment variables

Create a `.env` file in the repository root:

```env
DATABASE_URL=postgres://postgres:postgres@postgres:5432/undolog
UNDOLOG_LOG_LEVEL=info
UNDOLOG_PROXY_API_KEYS=sk-demo-key=org-demo
UNDOLOG_PROXY_UPSTREAM_TOOL_URL=http://tool-server:8080/tools
```

### 3. Build and start the stack

```bash
docker compose build --parallel
docker compose up -d
```

### 4. Verify all services are healthy

```bash
docker compose ps
```

Expected output:

```
NAME                IMAGE                               STATUS                    PORTS
ul-postgres-1       postgres:16-alpine                  Up (healthy)              0.0.0.0:5432->5432/tcp
ul-engine-1         ul-engine                           Up                        ...
ul-proxy-1          ul-proxy                            Up                        ...
ul-dashboard-1      ul-dashboard                        Up                        ...
```

### 5. Check health endpoints individually

```bash
# PostgreSQL
docker compose exec postgres pg_isready -U postgres

# Engine (HTTP health on 9090)
curl -s http://localhost:9090/

# Proxy (HTTP health on 8080)
curl -s http://localhost:8080/health

# Dashboard
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

Expected proxy health response:

```json
{"status":"ok","service":"undolog-proxy","engine_addr":"engine:50051","upstream_url":"http://tool-server:8080/tools"}
```

### 6. View logs

```bash
docker compose logs -f --tail=50
```

Filter by service:

```bash
docker compose logs -f proxy
docker compose logs -f engine
```

## Scaling considerations

- **Postgres**: Run on a dedicated host with persistent block storage. The
  `pgdata` volume mounts to `/var/lib/postgresql/data` inside the container.
- **Engine**: Stateless for interception; stateful for saga orchestrator. Run
  2+ replicas behind a load balancer for gRPC.
- **Proxy**: Stateless. Scale horizontally behind an HTTP reverse proxy (nginx,
  Caddy). Add `UPSTREAM_TOOL_URL` per replica.
- **Dashboard**: Stateless. One replica is sufficient for most deployments.

## Full environment reference

| Variable | Service | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | engine |, | PostgreSQL connection string (required) |
| `UNDOLOG_LOG_LEVEL` | all | `info` | Log verbosity |
| `UNDOLOG_ENGINE_GRPC_ADDR` | engine | `0.0.0.0:50051` | gRPC listen address |
| `UNDOLOG_ENGINE_HEALTH_ADDR` | engine | `0.0.0.0:9090` | Health HTTP listen address |
| `UNDOLOG_LOCK_MAX_ATTEMPTS` | engine | `3` | Advisory lock retries |
| `UNDOLOG_LOCK_RETRY_MS` | engine | `100` | Lock retry backoff |
| `UNDOLOG_REGISTRY_REFRESH_SECS` | engine | `60` | Tier registry refresh interval |
| `UNDOLOG_PROXY_LISTEN_ADDR` | proxy | `:8080` | HTTP listen address |
| `UNDOLOG_PROXY_ENGINE_GRPC_ADDR` | proxy | `engine:50051` | Engine gRPC address |
| `UNDOLOG_PROXY_UPSTREAM_TOOL_URL` | proxy |, | Upstream tool HTTP endpoint |
| `UNDOLOG_PROXY_API_KEYS` | proxy |, | Comma-separated `key=org` pairs |
| `UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS` | proxy | `30` | Graceful shutdown timeout |
| `NEXT_PUBLIC_PROXY_URL` | dashboard | `http://localhost:8080` | Proxy URL for API calls |
| `NEXT_PUBLIC_SSE_URL` | dashboard | `http://localhost:8080` | SSE endpoint URL |

## Verify it works

```bash
# Full stack health check
for svc in postgres engine proxy dashboard; do
  status=$(docker compose ps --format json "$svc" | jq -r '.State')
  echo "$svc: $status"
done

# Send a SAFE tool call through the proxy
curl -s -X POST http://localhost:8080/mcp/tool_call \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: sk-demo-key" \
  -H "X-Org-Id: org-demo" \
  -d '{"session_id":"test-001","tool_name":"search_web","step_index":1,"args":{"query":"hello"}}'
```

Expected response:

```json
{"status":"executed","effect_id":"...","result":...}
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `engine` exits immediately | `DATABASE_URL` missing or wrong | Verify `.env` contains the correct connection string |
| `proxy` exits with `connection refused` | Engine not ready | Add `depends_on: engine: condition: service_started` |
| `postgres` fails to start | Port 5432 in use | Change `ports: "5433:5432"` in compose file |
| Dashboard shows blank page | SSE or proxy URL wrong | Check `NEXT_PUBLIC_PROXY_URL` in compose environment |
| `docker compose build` fails | Missing submodules or Dockerfiles | Run from repository root; verify `infra/` directory |
| Disk space warning | Engine build layers cache | Run `docker builder prune --filter until=24h` |

## Next steps

- [Run UndoLog in production](running-in-production.md)
- [Configure PostgreSQL for production workloads](../reference/postgres-config.md)
- [Tune performance with advisory locks](../reference/performance-tuning.md)
