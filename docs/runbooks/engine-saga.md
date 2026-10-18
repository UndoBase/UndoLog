---
title: "Engine Saga Operational Runbook"
description: "Operational guide for effect TTL, dead-letter queue, session cache, rate limiting, approval timeouts, and registry age."
section: "runbooks"
---

# Engine Saga Operational Runbook

Operational guide for the engine saga subsystems: effect TTL retention,
dead-letter queue management, session cache tuning, rate limiting,
approval timeouts, and registry age.

---

## Effect TTL and Retention

Controls how long effect log entries are retained before deletion.

### Configuration

| Parameter | Default | Minimum | Description |
|-----------|---------|---------|-------------|
| `ttl_days` | 90 | 1 | Days before effects are eligible for deletion |

### How it works

1. The retention sweep runs via `RetentionStore::sweep()` on a configurable interval.
2. Effects with `executed_at` older than `now - ttl_days` are marked `deleted`.
3. Marked effects are physically removed on the next partition maintenance run.

### Operational tasks

**Count eligible effects for an org:**

```rust
let count = retention_store.count_eligible(org_id, &config).await?;
```

**Run a retention sweep:**

```rust
let result = retention_store.sweep(org_id, &config).await?;
// result.marked_for_deletion, result.deleted
```

### Tuning guidelines

| Scenario | Recommended TTL | Rationale |
|----------|----------------|-----------|
| Development / staging | 7 days | Fast cleanup, minimal storage |
| Production (default) | 90 days | Balance between audit trail and storage |
| Regulated environments | 365 days | Compliance requires long retention |

---

## Dead-Letter Queue

Manages compensations that failed permanently and require manual intervention.

### States

| State | Meaning | Available actions |
|-------|---------|-------------------|
| `failed` | Compensation failed after exhausting retries | `retry`, `skip` |
| `retrying` | Retry in progress | None (transient) |
| `skipped` | Manually resolved, no further action | None (terminal) |

### Operational tasks

**List all failed dead letters for an org:**

```sql
SELECT dead_letter_id, compensation_fn, error_message, retry_count, created_at
FROM undolog_dead_letters
WHERE org_id = '<org_id>' AND state = 'failed'
ORDER BY created_at DESC;
```

**Retry a failed compensation:**

```sql
UPDATE undolog_dead_letters
SET state = 'retrying', retry_count = retry_count + 1
WHERE dead_letter_id = '<id>' AND state = 'failed';
```

**Skip (resolve) a dead letter:**

```sql
UPDATE undolog_dead_letters
SET state = 'skipped'
WHERE dead_letter_id = '<id>' AND state = 'failed';
```

**Count dead letters by state:**

```sql
SELECT state, COUNT(*)
FROM undolog_dead_letters
WHERE org_id = '<org_id>'
GROUP BY state;
```

### When to intervene

| Symptom | Action |
|---------|--------|
| Dead letters accumulating | Check compensation function logs for root cause |
| Same function failing repeatedly | Review compensation logic for correctness |
| Dead letters older than 7 days | Consider skip if the underlying action is irrecoverable |

---

## Session Cache

In-memory cache that avoids a PostgreSQL round-trip on every `intercept` call.

### Configuration

| Parameter | Default | Minimum | Description |
|-----------|---------|---------|-------------|
| `ttl_secs` | 300 (5 min) | 1 | Seconds before a cached entry expires |
| `max_entries` | 10000 | 0 (unlimited) | Maximum cached entries; oldest evicted at capacity |

### How it works

1. On `intercept`, the engine checks the cache first (read lock).
2. Cache hit: returns immediately without a database query.
3. Cache miss: queries Postgres, inserts into cache.
4. State transitions (`committed`, `failed`, `compensating`) invalidate the entry.

### Operational tasks

**Monitor cache hit rate:**

Cache metrics are exposed via OpenTelemetry tracing. Look for spans with
`cache_hit = true` or `cache_hit = false` on the `intercept` operation.

**Detection signals:**

- OpenTelemetry span attributes: `cache_hit = true` or `cache_hit = false` on the `intercept` operation.
- Engine debug log lines:
  - `Session cache hit`: read lock fast path returns cached session record.
  - `Session cache miss`: triggers PostgreSQL database lookup on `undolog_sessions`.
  - `Session cache: TTL expired, evicting`: lazy eviction of an expired cache record.
  - `Session cache: evicting oldest entry at capacity`: capacity eviction when `max_entries` is exceeded.
  - `Session cache: invalidated`: explicit eviction on effect state transition.
- Metric indicators: elevated database query volume on `undolog_sessions` or increased `undolog_proxy_engine_rpc_duration_seconds`.

**Tune for high-throughput workloads:**

```rust
// Increase TTL and capacity for bursty traffic
CacheConfig::new(600, 50000)  // 10 min TTL, 50k entries
```

**Disable cache (debugging):**

```rust
// Set max_entries to 0 for unlimited, but ttl_secs to 1 for minimal caching
CacheConfig::new(1, 0)
```

### Recovery steps

1. If high cache miss rates or rapid evictions degrade performance, increase `max_entries` or `ttl_secs`.
2. Inspect client session patterns to ensure sessions are reused across related tool calls rather than creating single-use sessions.
3. If PostgreSQL experiences connection pressure from cache misses, verify connection pooling and the session table primary key index.

### Prevention steps

1. Size `max_entries` to twice the peak expected concurrent active sessions.
2. Maintain standard session reuse in client applications via `run_with_session`.
3. Monitor cache hit ratios in OpenTelemetry traces to identify session churn before database overload occurs.

### Tuning guidelines

| Workload | `ttl_secs` | `max_entries` | Rationale |
|----------|-----------|---------------|-----------|
| Low traffic | 300 | 10000 | Default is sufficient |
| High traffic | 600 | 50000 | Larger cache, longer TTL reduces DB load |
| Debugging | 1 | 0 | Minimal caching to isolate DB issues |

---

## Circuit Breaker and Rate Limiting

Protects the engine from cascading failures and provides backpressure.

### Configuration

| Parameter | Default | Minimum | Description |
|-----------|---------|---------|-------------|
| `error_threshold` | 5 | 1 | Consecutive errors before the circuit opens |
| `cooldown_secs` | 30 | 1 | Seconds to wait before half-opening |
| `max_concurrency` | 100 | 1 | Maximum concurrent intercept calls |

### Circuit breaker states

| State | Behavior |
|-------|----------|
| **Closed** | Normal operation. Errors counted. |
| **Open** | All requests rejected with `CircuitBreakerOpen`. |
| **HalfOpen** | One test request allowed. Success closes, failure re-opens. |

### Operational tasks

**Circuit breaker detection signals:**

- Engine logs:
  - `WARN circuit breaker: Closed -> Open (5 consecutive errors >= threshold 5)`
  - `WARN circuit breaker: request rejected (Open)`
  - `DEBUG circuit breaker: Open -> HalfOpen (cooldown elapsed)`
  - `DEBUG circuit breaker: HalfOpen -> Closed (success)`
  - `WARN circuit breaker: HalfOpen -> Open (failure, N consecutive)`
- Proxy metrics:
  - `undolog_proxy_engine_breaker_state`: Gauge reporting `0` (Closed), `1` (HalfOpen), or `2` (Open).
  - `undolog_proxy_engine_rejections_total{reason="breaker"}`: Counter incremented when calls are rejected.
- Error code: Callers receive `UndoLogError::CircuitBreakerOpen` or HTTP `503 Service Unavailable` with a `Retry-After` header.

**Concurrency limiter detection signals:**

- Engine logs: `DEBUG concurrency limit reached: N permits in use`.
- Proxy metrics:
  - `undolog_proxy_engine_inflight`: In-flight gRPC calls leaving the proxy for the engine.
  - `undolog_proxy_engine_queue_depth`: Calls waiting in the proxy admission queue.
  - `undolog_proxy_engine_rejections_total`: Incremented with `reason="gate"` (concurrency cap reached), `reason="queue"` (wait queue full), or `reason="wait"` (queue wait timeout).
- Error code: Callers receive `UndoLogError::ConcurrencyLimitReached` or HTTP `503 Service Unavailable` (`engine_busy`) with a `Retry-After` header.

**Tune for your workload:**

```rust
// Aggressive: open after 2 errors, 60s cooldown, 50 max concurrent
RateLimitConfig::new(2, 60, 50)

// Conservative: open after 10 errors, 10s cooldown, 200 max concurrent
RateLimitConfig::new(10, 10, 200)
```

### Recovery steps

1. When the circuit breaker opens:
   - Check engine logs to identify the root cause of consecutive failures (database connection drop, transaction deadlocks).
   - Resolve the underlying dependency failure.
   - The breaker automatically half-opens after `cooldown_secs` and probes engine health with a single request. Success returns the breaker to Closed.
2. When the concurrency limit is reached:
   - Check PgBouncer connection pool utilization and long-running PostgreSQL queries holding locks.
   - Throttle aggressive callers or configure per-key rate limiting (`UNDOLOG_PROXY_RATE_LIMIT_RPS`).
   - If PostgreSQL capacity allows, increase `max_concurrency` or add engine replicas.

### Prevention steps

1. Size proxy concurrency gate `UNDOLOG_PROXY_MAX_ENGINE_RPCS` below PgBouncer `default_pool_size` so requests do not queue on database connections.
2. Enable the bounded wait queue (`UNDOLOG_PROXY_ENGINE_QUEUE_MAX` and `UNDOLOG_PROXY_ENGINE_QUEUE_WAIT_MS`) to absorb short burst traffic without dropping calls.
3. Configure alerts in Prometheus on `undolog_proxy_engine_breaker_state == 2` and rising rejection counts.

---

## Approval Timeout

Handles pending approval requests that exceed their timeout window.

### Configuration

| Parameter | Default | Minimum | Description |
|-----------|---------|---------|-------------|
| `timeout_secs` | 86400 (24h) | 1 | Seconds before a pending approval times out |
| `auto_approve` | false | - | If true, timed-out approvals are auto-approved |
| `check_interval_secs` | 60 | 1 | How often the processor checks for timeouts |

### Operational tasks

**List pending approvals:**

```sql
SELECT approval_request_id, tool_name, created_at, timeout_at
FROM undolog_approval_requests
WHERE org_id = '<org_id>' AND state = 'pending'
ORDER BY created_at;
```

**Manually approve a pending request:**

```sql
UPDATE undolog_approval_requests
SET state = 'approved', resolved_at = now(), resolved_by = 'operator'
WHERE approval_request_id = '<id>' AND state = 'pending';
```

**Check auto-approve setting:**

```sql
SELECT auto_approve_on_timeout FROM undolog_approval_requests
WHERE approval_request_id = '<id>';
```

**Detection signals:**

- Background processor logs:
  - `INFO org_id=<org> processed=<count> "Approval timeout processor: handled timed-out approvals"`
  - `WARN org_id=<org> error=<error> "Approval timeout processor: failed to process timeouts"`
  - `WARN error=<error> "Approval timeout processor: failed to list orgs"`
- Proxy metrics:
  - `undolog_proxy_approval_decisions_total{action="timeout",result="applied"}`
  - `undolog_proxy_approval_decisions_total{action="timeout",result="conflict"}`
  - `undolog_proxy_approval_decision_duration_seconds`
- Database audit records: rows in `undolog_approval_events` with `action = 'timeout'`.

### Recovery steps

1. For urgent pending approvals approaching timeout, resolve them manually via the proxy `/approvals` endpoint or direct SQL update.
2. If the processor fails with database errors, inspect PostgreSQL connection pool capacity and lock contention.
3. If approvals timed out unintentionally and auto-approve was false, re-invoke the agent session.

### Prevention steps

1. Configure alerts on pending approvals older than 80% of `UNDOLOG_APPROVAL_TIMEOUT_SECS`.
2. Tune `UNDOLOG_APPROVAL_TIMEOUT_SECS` to match operational response timeframes.
3. Use SSE dashboard streams for real-time notification of approval requests to operators.

---

## Registry Age and Tool Reloading

Controls how tool definitions and reversibility tiers are refreshed from the database.

### Configuration

| Parameter | Default | Minimum | Description |
|-----------|---------|---------|-------------|
| `UNDOLOG_REGISTRY_REFRESH_SECS` | 15 | 5 | Seconds between background tool registry refresh runs |

### How it works

1. At startup, the engine queries `undolog_tool_registry` across all organizations and constructs an in-memory `TierRegistry`.
2. A background refresh loop periodically queries `undolog_tool_registry` for updates and atomically replaces the active registry.
3. The engine exposes an unauthenticated HTTP health endpoint on port 9090 reporting `registry_age_secs`.

### Operational tasks

**Monitor registry age via health endpoint:**

```bash
curl -fsS http://localhost:9090/
```

Expected response:

```json
{
  "status": "ok",
  "service": "undolog-engine",
  "registry_age_secs": 12
}
```

Before the first refresh completes, `registry_age_secs` is `null`.

**Detection signals:**

- Engine health endpoint: `registry_age_secs` steadily increases past twice `UNDOLOG_REGISTRY_REFRESH_SECS`.
- Log lines:
  - `INFO interval_secs=<secs> "Spawning registry refresh loop"`
  - `INFO total_tools=<count> "TierRegistry refreshed"`
  - `DEBUG org_id=<org> count=<count> "Registry refreshed for org"`
  - `WARN error=<error> "Registry refresh failed - keeping stale data"`
- Error signal: Newly added tools fail with `ToolNotRegistered` because the engine has not refreshed its registry.

### Recovery steps

1. Check engine logs for `Registry refresh failed - keeping stale data` and the underlying PostgreSQL error.
2. Verify PostgreSQL connectivity and SELECT permissions on the `undolog_tool_registry` table.
3. If the background refresh loop became wedged, restart the engine process.

### Prevention steps

1. Set up Prometheus alerting when `registry_age_secs > 3 * UNDOLOG_REGISTRY_REFRESH_SECS`.
2. Ensure database maintenance or migrations do not take exclusive table locks on `undolog_tool_registry`.
3. Keep `UNDOLOG_REGISTRY_REFRESH_SECS` at or above the 5 second minimum.

---

## See also

- [Failure Mode Runbook](failure-modes.md): error code reference
- [ADR 0012: Effect TTL and Retention](../adr/0012-effect-ttl-retention.md)
- [ADR 0013: Dead-Letter Queue](../adr/0013-dead-letter-queue.md)
- [ADR 0014: Crash Recovery Testing](../adr/0014-saga-crash-recovery-testing.md)
