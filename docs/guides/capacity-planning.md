---
title: "Capacity Planning and Sizing Guide"
description: "Plan storage growth, connection pools, replica scaling, retention periods, and admission limits for production UndoLog deployments."
section: "guides"
---
# Capacity Planning and Sizing Guide

This guide provides formulas and reference figures for sizing UndoLog
deployments in production. It covers PostgreSQL connection pool allocation,
horizontal replica scaling, effect log storage growth, retention levers, and
proxy backpressure limits.

---

## 1. Connection Pool Sizing

The gRPC engine executes transactions against PostgreSQL for every tool call
intercept, commit, and approval resolution. Production deployments must place
PgBouncer in transaction pooling mode in front of PostgreSQL.

### Allocation Formula

Size your connection layers according to the following constraints:

```
Proxy Concurrency Cap (UNDOLOG_PROXY_MAX_ENGINE_RPCS)
  <= PgBouncer default_pool_size - Background Headroom
  < PostgreSQL max_connections
```

Recommended allocations:

| Component | Setting | Recommended Value | Rationale |
|---|---|---|---|
| PostgreSQL | `max_connections` | 100 | Dedicated to PgBouncer server connections and administrative sessions. |
| PgBouncer | `pool_mode` | `transaction` | Releases database connections immediately when transactions complete. |
| PgBouncer | `max_client_conn` | 200 | Maximum client connections accepted from all engine replicas. |
| PgBouncer | `default_pool_size` | 25 | Active server connections held open to PostgreSQL. |
| Engine Proxy Gate | `UNDOLOG_PROXY_MAX_ENGINE_RPCS` | 20 | Caps aggregate in-flight calls leaving proxy, leaving 5 slots for background tasks. |

### Background Task Headroom

Reserve at least 5 connections in `default_pool_size` for engine background
tasks:

1. **Registry Refresh:** The background loop periodically queries `undolog_tool_registry`
   every `UNDOLOG_REGISTRY_REFRESH_SECS` (default: 15s).
2. **Approval Timeout Processor:** Scans `undolog_approval_requests` every
   `UNDOLOG_TIMEOUT_CHECK_INTERVAL_SECS` (default: 60s).
3. **Retention Sweeps:** Periodic queries on `undolog_effect_log` during
   TTL cleanup runs.

Pointing `DATABASE_URL` directly to PostgreSQL instead of PgBouncer causes
connection exhaustion under burst traffic. Always connect through PgBouncer:

```env
DATABASE_URL=postgres://postgres:postgres@pgbouncer:6432/undolog
```

---

## 2. Horizontal Replica Scaling

UndoLog scales the proxy and engine layers independently as stateless units.

### Scaling the Proxy Layer

The Go proxy is an HTTP and SSE ingress service:

- **Stateless Ingress:** Proxy replicas do not coordinate directly with each other.
  Deploy behind an L7 load balancer (Nginx, Caddy, AWS ALB, or Kubernetes Ingress).
- **Session Stickiness:** Not required. Tool calls carrying identical session IDs
  can arrive at different proxy replicas without state divergence.
- **SSE Dashboard Streams:** Each proxy instance maintains its own local SSE
  broadcaster. Operators connecting to the dashboard can connect to any proxy replica.

### Scaling the Engine Layer

The Rust effect engine is a stateless gRPC service:

- **Load Balancing:** Deploy multiple engine replicas behind a gRPC-aware load
  balancer using round-robin or least-requests routing.
- **Session Cache Distribution:** Each engine replica maintains an in-memory
  `SessionCache`. Cache misses fall back to PostgreSQL and populate the local
  replica cache automatically.

### Advisory Lock Behavior Across Replicas

Concurrent tool calls with identical call signatures serialize using PostgreSQL
transaction-level advisory locks (`pg_advisory_xact_lock`):

- **Cluster-Global Synchronization:** PostgreSQL advisory locks are managed
  globally by the database catalog. Even when two identical tool calls arrive
  at different engine replicas simultaneously, PostgreSQL grants the lock to one
  transaction only. The second transaction waits for lock release, sees the
  committed record, and returns the replay result without duplicate execution.
- **Lock Contention Tuning:** Tune the lock retry parameters in the engine
  environment when running multiple replicas under high concurrent load on
  identical call signatures:
  - `UNDOLOG_LOCK_MAX_ATTEMPTS`: Number of acquisition attempts before
    failing (default: `5`).
  - `UNDOLOG_LOCK_RETRY_MS`: Backoff interval between attempts (default: `50`).
    Under heavy multi-replica contention, increase to `100` or `200` to give
    executing transactions time to commit.

---

## 3. Storage Growth and Partition Sizing

The effect log (`undolog_effect_log`) is an append-only journal capturing every
tool invocation. Planning disk capacity requires understanding row sizing and
partition lifecycles.

### Measured Row Footprint

Based on schema layout and benchmark measurements against a seeded PostgreSQL 16
table:

| Component | Average Size | Description |
|---|---|---|
| Fixed Columns | 174 bytes | UUIDs, BLAKE3 signature, timestamps, enums, step index. |
| Variable Columns | 725 bytes | JSONB payloads: `args_snapshot`, `result_snapshot`, `compensation_args`. |
| Row Header and Alignment | 32 bytes | PostgreSQL tuple header and padding bytes. |
| **Raw Heap Row Total** | **~930 bytes** | Average table row size on disk. |
| Index Overhead | ~350 bytes | B-tree on session/step, partial state index, unique signature, and BRIN index. |
| **Combined Total Footprint** | **~1.28 KB** | Total storage required per tool call effect (~1.3 KB). |

### Storage Projections

Storage growth scales directly with daily tool call volume:

| Daily Tool Calls | Daily Growth | Monthly Partition Size | 90-Day Retention Footprint |
|---|---|---|---|
| 100,000 | 130 MB | ~3.9 GB | ~11.7 GB |
| 500,000 | 650 MB | ~19.5 GB | ~58.5 GB |
| 1,000,000 | 1.3 GB | ~39.0 GB | ~117.0 GB |
| 5,000,000 | 6.5 GB | ~195.0 GB | ~585.0 GB |
| 10,000,000 | 13.0 GB | ~390.0 GB | ~1.17 TB |

### Partition and Retention Levers

To prevent unbounded storage growth, use the following operational levers:

1. **Monthly Range Partitions:**
   Keep monthly partitions enabled on `undolog_effect_log`:
   ```sql
   CREATE TABLE undolog_effect_log_2026_07 PARTITION OF undolog_effect_log
     FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
   CREATE INDEX idx_vel_2026_07_brin ON undolog_effect_log_2026_07
     USING BRIN (executed_at) WITH (pages_per_range = 32);
   CREATE UNIQUE INDEX idx_vel_2026_07_sig ON undolog_effect_log_2026_07 (call_signature);
   ```
2. **Instant Partition Drops:**
   Instead of running resource-intensive `DELETE` queries that cause table
   bloat, drop partitions that have aged past your retention policy:
   ```sql
   DROP TABLE undolog_effect_log_2026_04;
   ```
   Dropping a partition takes less than 100ms, frees disk space immediately to
   the operating system, and requires zero VACUUM processing.
3. **Software Retention Sweeps:**
   For granular effect deletion within an active partition, configure `ttl_days`
   (default: `90`). The retention worker flags eligible records for removal.

---

## 4. Admission and Backpressure Levers

Protect downstream database connections from overload by tuning the proxy's
admission layers. For architectural details and status code definitions, see
the [Production Deployment Guide](running-in-production.md#7-tune-backpressure-and-rate-limiting).

### Configuration Levers

Configure these environment variables on each proxy instance:

| Variable | Default | Recommended Starting Value | Purpose |
|---|---|---|---|
| `UNDOLOG_PROXY_RATE_LIMIT_RPS` | `0` (disabled) | `50` | Sustained rate limit per API key digest (HTTP 429). |
| `UNDOLOG_PROXY_RATE_LIMIT_BURST` | `0` (disabled) | `100` | Maximum burst capacity per API key token bucket. |
| `UNDOLOG_PROXY_MAX_ENGINE_RPCS` | `0` (unbounded) | `20` | Concurrency cap for in-flight calls leaving proxy for engine. |
| `UNDOLOG_PROXY_ENGINE_QUEUE_MAX` | `0` (disabled) | `64` | FIFO wait queue depth for holding micro-bursts (HTTP 503). |
| `UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS` | `0` (disabled) | `250` | Maximum duration a call may wait for a gate slot. |
| `UNDOLOG_PROXY_ENGINE_BREAKER_THRESHOLD` | `0` (disabled) | `5` | Consecutive transport errors before circuit opens. |
| `UNDOLOG_PROXY_ENGINE_BREAKER_RESET_SECS` | `0` (disabled) | `30` | Seconds to wait before probing engine health with single call. |

### Admission Order

The proxy enforces admission across layers in strict order:
1. Per-key token bucket at the ingress edge (returns 429 if rate exceeded).
2. FIFO wait queue to absorb micro-bursts without immediate rejection.
3. Concurrency cap slot acquisition protecting PgBouncer pool.
4. Circuit breaker evaluation fast-failing traffic during engine outages (returns 503).

---

## 5. Capacity Planning Checklist

Before promoting UndoLog to production:

- [X] PostgreSQL connection pool mode set to `transaction` in PgBouncer.
- [X] `UNDOLOG_PROXY_MAX_ENGINE_RPCS` set below PgBouncer `default_pool_size`.
- [X] Monthly partitions created at least 3 months in advance.
- [X] BRIN indexes (`pages_per_range = 32`) verified on all partition tables.
- [X] Automated cron or orchestrator job scheduled to drop expired partitions.
- [X] Disk alerting configured at 75% capacity based on daily growth projections.
- [X] Prometheus metrics scraped from proxy `/metrics` and engine `:9090/`.

---

## See also

- [How to run UndoLog in production](running-in-production.md): TLS, health probes, and backpressure tuning
- [PostgreSQL high availability](postgresql-ha.md): Streaming replication, read replicas, and failover
- [Database schema reference](../reference/database-schema.md): Complete table layouts and index definitions
- [Service Level Objectives](../reference/slo.md): Latency and throughput benchmarks
