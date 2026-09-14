---
title: "Multi-Region Strategy"
description: "Kafka WAL, conflict resolution, and cost estimate for multi-region active-active."
section: "design"
---
# Multi-Region Strategy

Kafka WAL, conflict resolution, and cost estimate for multi-region
active-active.

---

## Architecture

```
Region A (us-east-1)          Region B (eu-west-1)
┌─────────────────┐           ┌─────────────────┐
│  Proxy          │           │  Proxy          │
│  (Go)           │           │  (Go)           │
└────────┬────────┘           └────────┬────────┘
         │                             │
         v                             v
┌─────────────────┐           ┌─────────────────┐
│  Engine         │           │  Engine         │
│  (Rust)         │           │  (Rust)         │
└────────┬────────┘           └────────┬────────┘
         │                             │
         v                             v
┌─────────────────┐           ┌─────────────────┐
│  PostgreSQL     │           │  PostgreSQL     │
│  (primary)      │           │  (primary)      │
└────────┬────────┘           └────────┬────────┘
         │                             │
         └──────────┬──────────────────┘
                    │
                    v
            ┌───────────────┐
            │  Kafka Cluster│
            │  (3 brokers)  │
            └───────────────┘
```

Each region runs a complete stack. Writes are published to Kafka and
consumed by all regions.

---

## Write path

1. Tool call arrives at proxy in Region A
2. Proxy calls engine `Intercept` in Region A
3. Engine writes to local PostgreSQL (effect log, undo stack)
4. Engine publishes event to Kafka topic `undolog.effects`
5. Kafka replicates to Region B
6. Region B consumer applies the event to local PostgreSQL
7. Region B is now eventually consistent (< 500ms typically)

Reads (intercept replay, approval status) are served from local
PostgreSQL with no cross-region latency.

---

## Conflict resolution

### Last-writer-wins (LWW)

When two regions update the same effect concurrently:

1. Each write includes a logical timestamp (Kafka message offset)
2. The consumer compares timestamps
3. The later write wins; the earlier write is logged but discarded

This is acceptable for UndoLog because:

- Effect logs are append-only; the losing write is an audit entry, not
  data loss
- Concurrent writes to the same effect are rare (different sessions
  usually touch different effects)
- The alternative (distributed locks) adds 100-300ms latency per write

### Session-scoped isolation

Sessions are scoped to a single region. A session that starts in
Region A stays in Region A. This eliminates most cross-region conflicts
because:

- Different sessions write to different effect IDs
- The same session never writes from two regions simultaneously
- Approval requests are regional (human approves in the dashboard
  closest to them)

---

## Kafka configuration

### Topic: `undolog.effects`

| Property | Value |
|----------|-------|
| Partitions | 12 (one per region x effect type) |
| Replication factor | 3 (across AZs) |
| Retention | 7 days |
| Cleanup policy | Delete |

### Consumer group: `undolog-engine`

| Property | Value |
|----------|-------|
| Group ID | `undolog-engine-{region}` |
| Auto offset reset | Earliest |
| Max poll records | 100 |
| Session timeout | 30s |

---

## Cost estimate

### Kafka cluster (3 brokers, multi-AZ)

| Component | Monthly cost |
|-----------|--------------|
| 3x kafka.m5.large | $430 |
| EBS gp3 500GB x3 | $180 |
| Data transfer (1TB/mo) | $90 |
| **Total Kafka** | **$700** |

### Per additional region

| Component | Monthly cost |
|-----------|--------------|
| Engine (c5.xlarge) | $140 |
| PostgreSQL (r5.large) | $180 |
| Proxy (c5.large) | $70 |
| Data transfer (500GB) | $45 |
| **Total per region** | **$435** |

### Example: 2 regions

| Component | Monthly cost |
|-----------|--------------|
| Kafka cluster | $700 |
| Region A | $435 |
| Region B | $435 |
| **Total** | **$1,570** |

---

## Migration path

### Phase 1: Single region (current)

- One PostgreSQL instance, one engine, one proxy
- No Kafka dependency

### Phase 2: Add Kafka

- Deploy Kafka cluster alongside existing PostgreSQL
- Engine publishes events but does not consume yet
- No behavioral change; Kafka is fire-and-forget

### Phase 3: Enable consumption

- Deploy second region with full stack
- Enable consumer in both regions
- Validate consistency with integration tests

### Phase 4: Full active-active

- Both regions handle production traffic
- Monitor consumer lag and conflict rates
- Tune Kafka retention and partition count

---

## Limitations

1. **Eventual consistency.** Cross-region replication takes 100-500ms.
   Reads in Region B may be stale after a write in Region A.

2. **LWW conflicts.** Concurrent writes to the same effect lose one
   write. This is logged but not recoverable.

3. **Session affinity.** Sessions must be routed to the same region.
   Cross-region session migration is not supported.

4. **Kafka dependency.** Kafka outage isolates regions. Mitigation:
   multi-AZ deployment and consumer lag monitoring.

---

## See also

- [ADR 0011](../adr/0011-multi-region-active-active.md): decision record
- [Effect states](../reference/effect-states.md): state machine
- [PostgreSQL advisory locks](../adr/0004-postgresql-advisory-locks.md):
  current deduplication approach
