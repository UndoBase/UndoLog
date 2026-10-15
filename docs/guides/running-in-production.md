---
title: "How to run UndoLog in production"
description: "Prepare UndoLog for production: TLS, connection pooling, monitoring, backup, and failure handling."
section: "guides"
---
# How to run UndoLog in production

## Prerequisites

- [Deployed with Docker Compose](deploying-with-docker.md)
- PostgreSQL 16 with connection pooling
- A reverse proxy (nginx, Caddy, or AWS ALB) terminating TLS
- Monitoring system (Prometheus / Grafana or equivalent)

## What you'll build

You will configure the UndoLog stack for production: tune PostgreSQL,
authenticate the engine's gRPC endpoint, harden the proxy with API keys,
set timeouts on the engine, and verify graceful shutdown.

## When to prefer the split deployment

UndoLog ships two topologies: one container running both programs (see
[How to deploy UndoLog with Docker](deploying-with-docker.md)) and the
split stack this guide describes. The combined unit is for single-node
installs with no orchestration. Keep the split deployment when:

- You scale either side independently: replicas, per-process resource
  limits, or a rolling restart of one service only.
- You need separate failure domains: a proxy crash must not restart the
  engine, and the two log streams must stay separable.
- You run the Helm chart or another orchestrator, which drives the split
  services exactly as configured here.

## Steps

### 1. Configure PostgreSQL for production

Connect PgBouncer in transaction mode in front of PostgreSQL:

```ini
; pgbouncer.ini
[databases]
undolog = host=localhost port=5432 dbname=undolog

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
pool_mode = transaction
max_client_conn = 200
default_pool_size = 25
```

Set the `DATABASE_URL` to point through PgBouncer:

```env
DATABASE_URL=postgres://postgres:postgres@pgbouncer:6432/undolog
```

Create BRIN indexes for the effect log, which grows monotonically:

```sql
CREATE INDEX IF NOT EXISTS idx_effects_org_created
  ON undolog_effects USING BRIN (org_id, created_at)
  WITH (pages_per_range = 32);
```

Partition the effect log table by month for large volumes:

```sql
CREATE TABLE undolog_effects_y2026m05
  PARTITION OF undolog_effects
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
```

### 2. Configure the engine

```env
# Database
DATABASE_URL=postgres://postgres:postgres@pgbouncer:6432/undolog

# Lock tuning: reduce contention on hot rows
UNDOLOG_LOCK_MAX_ATTEMPTS=5
UNDOLOG_LOCK_RETRY_MS=50

# Registry refresh: how often tools are reloaded from the DB
UNDOLOG_REGISTRY_REFRESH_SECS=120

# Logging
UNDOLOG_LOG_LEVEL=info

# gRPC authentication: rejects calls that do not present the token
UNDOLOG_ENGINE_AUTH_TOKEN=replace-with-a-long-random-value
```

With `UNDOLOG_ENGINE_AUTH_TOKEN` unset or blank, the engine's gRPC
endpoint accepts every call, which keeps local development unchanged.
In production set a long random value: every RPC must then carry it in
the `authorization` metadata as `Bearer <token>`, and a call without it
is rejected with `UNAUTHENTICATED`. Rotation is an environment change
followed by an engine restart.

### 3. Configure the proxy

```env
# API keys: one or more key=org pairs
UNDOLOG_PROXY_API_KEYS=sk-prod-1=org-acme,sk-prod-2=org-beta

# Upstream tool endpoint (your MCP tool server)
UNDOLOG_PROXY_UPSTREAM_TOOL_URL=http://tool-server:8080/tools

# Timeouts
UNDOLOG_PROXY_READ_TIMEOUT_SECS=10
UNDOLOG_PROXY_WRITE_TIMEOUT_SECS=10
UNDOLOG_PROXY_REQUEST_TIMEOUT_SECS=30
UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS=20

# Engine authentication: must equal the engine's UNDOLOG_ENGINE_AUTH_TOKEN
UNDOLOG_PROXY_ENGINE_AUTH_TOKEN=replace-with-a-long-random-value
```

The proxy sends `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` on every engine gRPC
call as `Bearer <token>`, and sends nothing when it is unset or blank.
Set the same value on both sides and restart both to rotate it. If the
values differ, the engine rejects every call: tool calls fail with a
502 whose message names `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN`, and the
proxy logs the same message for approval decisions, so a mismatch is
visible from the first failed request.

### 4. Set up TLS termination

Run an nginx reverse proxy in front of `undolog-proxy`:

```nginx
server {
    listen 443 ssl;
    server_name undolog.example.com;

    ssl_certificate /etc/letsencrypt/live/undolog.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/undolog.example.com/privkey.pem;

    location /mcp/tool_call {
        proxy_pass http://proxy:8080;
        proxy_set_header X-Org-Id $http_x_org_id;
    }

    location /approvals {
        proxy_pass http://proxy:8080;
    }

    location /events {
        proxy_pass http://proxy:8080;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
    }
}
```

### 5. Network isolation

Do not expose the engine's gRPC port (50051) or health port (9090) to the
internet. The proxy connects to the engine over an internal Docker network.

```yaml
# docker-compose.yml
networks:
  internal:
    driver: bridge
  public:
    driver: bridge

services:
  engine:
    networks:
      - internal
  proxy:
    networks:
      - internal
      - public
  nginx:
    networks:
      - public
```

### 6. Monitor health endpoints

Set up your orchestrator to probe the health endpoints. Both the engine and the
proxy images are intentionally shell-free (distroless / scratch), so a compose
`healthcheck` cannot run `wget` or `curl` inside those containers. Probe from
the host or the orchestrator instead, every 15 seconds:

```bash
# Engine (HTTP liveness on 9090; answers on any path)
curl -fsS -o /dev/null http://<engine-host>:9090/

# Proxy liveness: the process is up
curl -fsS http://<proxy-host>:8080/health

# Proxy readiness: the engine channel is connected (503 until it is)
curl -fsS http://<proxy-host>:8080/readyz
```

The proxy reports `{"status":"ok","service":"undolog-proxy"}` once HTTP is up.
Use `/health` as the liveness probe and gate traffic on `/readyz`, which
answers 200 only while the engine channel is connected: 503 before the first
connect or while the engine is down. The first `/readyz` call or tool call
opens the channel, so readiness converges without traffic, and the channel
reconnects on its own: liveness never depends on the engine being online.

### 7. Graceful shutdown

Both the proxy and the engine handle SIGINT and SIGTERM. On the signal the
proxy closes dashboard event streams, stops accepting connections, and
drains in-flight requests within `UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS`
(default 30s). A drain that finishes inside the window exits with no
shutdown log line; a drain that runs out logs `shutdown error` at WARN with
the error and exits anyway. The engine stops accepting new gRPC
connections, finishes in-flight RPCs, aborts its health endpoint, and logs
`UndoLog Engine stopped` at INFO. Neither process puts a deadline on its
own drain, so keep RPCs shorter than the signal grace period your
orchestrator allows. Docker Compose escalates to SIGKILL 10 seconds after
`stop` or `down` by default, which `stop_grace_period` raises.

Both processes log JSON to stdout, so the commands below show shutdown
warnings as they happen.

Test graceful shutdown:

```bash
docker compose kill -s SIGTERM proxy
docker compose logs proxy --tail=10
```

A clean drain adds no `shutdown error` line to that output. To see the WARN
line, send the signal while a request is in flight and lower
`UNDOLOG_PROXY_SHUTDOWN_TIMEOUT_SECS` to 1.

### 8. Backup Postgres

Use `pg_dump` for logical backups and WAL archiving for point-in-time recovery:

```bash
# Daily logical backup
docker compose exec -T postgres pg_dump -U postgres undolog \
  | gzip > /backups/undolog-$(date +%Y%m%d).sql.gz

# WAL archiving (in postgres.conf)
archive_mode = on
archive_command = 'cp %p /wal_archive/%f'
```

## Verify it works

```bash
# Full production health check
curl -s https://undolog.example.com/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "undolog-proxy"
}
```

The proxy health endpoint is a liveness probe that does not echo configuration
values. For deeper visibility, scrape `/metrics` (Prometheus text format) from
every proxy instance:

```yaml
scrape_configs:
  - job_name: undolog-proxy
    metrics_path: /metrics
    static_configs:
      - targets: ["proxy:8080"]
```

The metric set covers HTTP traffic by route and status, engine RPC latency and
errors, SSE subscriber and dropped-event counts, approval decision latency, and
upstream tool executor latency. The metrics endpoint is unauthenticated like
`/health`, so scope it behind a reverse-proxy rule if it must not be public.

```bash
# Confirm TLS
curl -svI https://undolog.example.com/health 2>&1 | grep "SSL connection"
```

Expected:

```
* SSL connection using TLSv1.3
```

```bash
# Confirm PgBouncer pool
docker compose exec pgbouncer psql -h localhost -p 6432 -U postgres -c "SHOW POOLS;"
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Proxy returns 502 on tool calls | `UPSTREAM_TOOL_URL` unreachable | Verify the tool server is running and reachable from the proxy container |
| Engine health check fails | `DATABASE_URL` points to wrong host | Ensure the engine connects through PgBouncer, not directly to Postgres |
| High lock contention | `UNDOLOG_LOCK_RETRY_MS` too low | Increase to 100–200ms; verify BRIN index is in place |
| SSE dashboard disconnects | Reverse proxy buffers SSE | Set `proxy_buffering off; proxy_cache off;` for `/events` |
| API key rejected | Key not in `UNDOLOG_PROXY_API_KEYS` | Restart proxy after changing env var; keys are loaded at startup |
| 502 naming `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` | Proxy token missing or different from the engine's | Set the same value in `UNDOLOG_PROXY_ENGINE_AUTH_TOKEN` and `UNDOLOG_ENGINE_AUTH_TOKEN`, then restart both |
| gRPC connection reset | Engine restart | Proxy reconnects automatically: the engine connection is created lazily and self-healing, so the next RPC reaches the restarted engine. The retry config bounds Commit/Fail retries, not the connection |

## Next steps

- [PostgreSQL high availability](postgresql-ha.md): WAL streaming, read replicas, failover, and monitoring
- [Database comparison](../reference/database-comparison.md): feature comparison across PostgreSQL, SQLite, and MySQL
- [Monitor the SSE dashboard for live effects](../reference/api/go-proxy-rest.md)
- [Set up alerting on `compensation_failed` events](opentelemetry-setup.md)
