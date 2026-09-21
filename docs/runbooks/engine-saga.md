---
title: "Engine Saga Operational Runbook"
description: "Operational guide for effect TTL, dead-letter queue, session cache, and rate limiting."
section: "runbooks"
---

# Engine Saga Operational Runbook

Operational guide for the engine saga subsystems: effect TTL retention,
dead-letter queue management, session cache tuning, and rate limiting.

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

**Monitor circuit breaker state:**

Circuit breaker transitions are logged at `WARN` and `DEBUG` levels:

```
WARN circuit breaker: Closed -> Open (5 consecutive errors >= threshold 5)
WARN circuit breaker: request rejected (Open)
DEBUG circuit breaker: Open -> HalfOpen (cooldown elapsed)
DEBUG circuit breaker: HalfOpen -> Closed (success)
```

**Tune for your workload:**

```rust
// Aggressive: open after 2 errors, 60s cooldown, 50 max concurrent
RateLimitConfig::new(2, 60, 50)

// Conservative: open after 10 errors, 10s cooldown, 200 max concurrent
RateLimitConfig::new(10, 10, 200)
```

### When the circuit is open

1. Check engine logs for the error that caused the consecutive failures.
2. Fix the underlying issue (database connectivity, dependency failure).
3. The circuit will automatically half-open after `cooldown_secs`.
4. If the issue persists, the circuit will re-open after a single failure.

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

---

## See also

- [Failure Mode Runbook](failure-modes.md) - Error code reference
- [ADR 0012: Effect TTL and Retention](../adr/0012-effect-ttl-retention.md)
- [ADR 0013: Dead-Letter Queue](../adr/0013-dead-letter-queue.md)
- [ADR 0014: Crash Recovery Testing](../adr/0014-saga-crash-recovery-testing.md)
