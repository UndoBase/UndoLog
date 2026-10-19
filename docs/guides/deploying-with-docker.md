---
title: "How to deploy UndoLog with Docker"
description: "Deploy the full UndoLog stack with Docker Compose: PostgreSQL 16, the Rust engine, the Go proxy, and the dashboard."
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

Images are built locally from the repository clone. Pre-built container
images are not published to a public registry today, so `docker compose build`
compiles each component (`engine`, `proxy`, and `dashboard`) directly from
source using the Dockerfiles in `infra/`.

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
UNDOLOG_PROXY_UPSTREAM_TOOL_URL=http://tool-server:9091/tools
```

### 3. Build and start the stack

Because no public container registry publishes pre-built images, compile the
service images locally before launching the containers:

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
{"status":"ok","service":"undolog-proxy"}
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

## Run the proxy and the engine in one container

For a single node that does not need two units, an override compose file
runs both programs in one container beside PostgreSQL:

```bash
docker compose -f docker-compose.yml -f infra/docker-compose.single.yml up -d
```

The override rebuilds the `proxy` service on a combined image
(`infra/Dockerfile.undolog`) that carries both binaries. Inside the
container the proxy reaches the engine on `127.0.0.1:50051`, so no gRPC
port is published; the engine's health endpoint shares the container on
9090 next to the proxy's 8080. The split `engine` service is held behind
the unselected `split` profile, so the two topologies never start
together. The base file on its own is unchanged: it still runs the split
stack described above.

The same verification commands work, because both health endpoints
answer from the one container. Stopping it signals both processes:

```bash
docker compose -f docker-compose.yml -f infra/docker-compose.single.yml stop proxy
docker compose -f docker-compose.yml -f infra/docker-compose.single.yml logs proxy
```

The logs show the engine's `UndoLog Engine stopped` line plus an exit
status line per process. This path needs Docker Compose 2.24.4 or newer
for the `!override` tag the file uses to replace the proxy's dependency
on the split engine.

## Scaling considerations

- **Postgres**: Run on a dedicated host with persistent block storage. The
  `pgdata` volume mounts to `/var/lib/postgresql/data` inside the container.
- **Engine**: Stateless for interception; stateful for saga orchestrator. Run
  2+ replicas behind a load balancer for gRPC.
- **Proxy**: Stateless. Scale horizontally behind an HTTP reverse proxy (nginx,
  Caddy). Add `UNDOLOG_PROXY_UPSTREAM_TOOL_URL` per replica.
- **Dashboard**: Stateless. One replica is sufficient for most deployments.

## Full environment reference

| Variable | Service | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | engine | `postgresql://postgres:postgres@localhost:5432/undolog_dev` | PostgreSQL connection string; compose sets it |
| `UNDOLOG_LOG_LEVEL` | all | `info` | Log verbosity |
| `UNDOLOG_ENGINE_GRPC_ADDR` | engine | `0.0.0.0:50051` | gRPC listen address |
| `UNDOLOG_ENGINE_HEALTH_ADDR` | engine | `0.0.0.0:9090` | Health HTTP listen address |
| `UNDOLOG_LOCK_MAX_ATTEMPTS` | engine | `3` | Advisory lock retries |
| `UNDOLOG_LOCK_RETRY_MS` | engine | `100` | Lock retry backoff |
| `UNDOLOG_REGISTRY_REFRESH_SECS` | engine | `15` | Tier registry refresh interval (minimum 5 seconds) |
| `UNDOLOG_PROXY_LISTEN_ADDR` | proxy | `:8080` | HTTP listen address |
| `UNDOLOG_PROXY_ENGINE_GRPC_ADDR` | proxy | `localhost:50051` | Engine gRPC address; compose sets `engine:50051` |
| `UNDOLOG_PROXY_UPSTREAM_TOOL_URL` | proxy | `none` | Upstream tool HTTP endpoint; compose sets the tool server |
| `UNDOLOG_PROXY_API_KEYS` | proxy | `none` | Comma-separated `key=org` pairs; at least one is required |
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
| `proxy` logs `connection refused` | Engine not yet ready | Normal during startup: the proxy connects lazily on the first RPC and reconnects automatically after the engine (re)starts, so it recovers without a proxy restart |
| `postgres` fails to start | Port 5432 in use | Change `ports: "5433:5432"` in compose file |
| Dashboard shows blank page | SSE or proxy URL wrong | Check `NEXT_PUBLIC_PROXY_URL` in compose environment |
| `docker compose build` fails | Missing submodules or Dockerfiles | Run from repository root; verify `infra/` directory |
| Disk space warning | Engine build layers cache | Run `docker builder prune --filter until=24h` |

## Next steps

- [Run UndoLog in production](running-in-production.md)
- [Configure PostgreSQL for production workloads](postgresql-ha.md)
