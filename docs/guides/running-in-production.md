---
title: "How to run UndoLog in production"
description: "## Prerequisites"
section: "guides"
---
# How to run UndoLog in production

## Prerequisites

- [Deployed with Docker Compose](deploying-with-docker.md)
- PostgreSQL 16 with connection pooling
- A reverse proxy (nginx, Caddy, or AWS ALB) terminating TLS
- Monitoring system (Prometheus / Grafana or equivalent)

## What you'll build

You will configure the UndoLog stack for production: tune PostgreSQL, harden
the proxy with API keys, set timeouts on the engine, and verify graceful
shutdown.

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
```

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
```

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

Set up your orchestrator to probe every 15 seconds:

```yaml
# Engine
healthcheck:
  test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:9090/"]
  interval: 15s
  timeout: 3s
  retries: 3

# Proxy
healthcheck:
  test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:8080/health"]
  interval: 15s
  timeout: 3s
  retries: 3
```

### 7. Graceful shutdown

Both the proxy and engine handle SIGINT and SIGTERM. The proxy drains in-flight
requests within `SHUTDOWN_TIMEOUT_SECS` (default 30s). The engine closes gRPC
connections within 10s.

Test graceful shutdown:

```bash
docker compose kill -s SIGTERM proxy
docker compose logs proxy --tail=10
```

Expected log output:

```
{"level":"info","msg":"shutdown initiated","signal":"SIGTERM"}
{"level":"info","msg":"draining active requests","count":0}
{"level":"info","msg":"shutdown complete"}
```

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
  "service": "undolog-proxy",
  "engine_addr": "engine:50051",
  "upstream_url": "http://tool-server:8080/tools"
}
```

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
| gRPC connection reset | Engine restart | Proxy reconnects automatically with retry; check `MaxAttempts: 3` in proxy config |

## Next steps

- [Monitor the SSE dashboard for live effects](../reference/dashboard.md)
- [Tune PostgreSQL for UndoLog workloads](../reference/postgres-config.md)
- [Set up alerting on `compensation_failed` events](../reference/monitoring.md)
