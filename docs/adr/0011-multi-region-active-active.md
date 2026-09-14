---
title: "ADR 0011: Multi-Region Active-Active Architecture"
description: "- **Date:** 2026-09-14 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0011: Multi-Region Active-Active Architecture

- **Date:** 2026-09-14
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

UndoLog currently operates as a single-region deployment. The engine
runs in one AWS region with a single PostgreSQL instance. This creates
two problems:

1. **Latency.** Global users experience 100-300ms round-trip latency to
   the engine for every tool call interception. For latency-sensitive
   agent workflows, this overhead is unacceptable.

2. **Availability.** A regional outage (AWS us-east-1, for example)
   takes down all UndoLog instances worldwide. There is no failover
   path.

The infrastructure roadmap identifies multi-region active-active as a
Phase 3 goal. This ADR records the decision to pursue active-active
replication with Kafka as the write-ahead log.

## Decision

UndoLog adopts a multi-region active-active architecture with Kafka as
the cross-region event log. Each region runs a complete UndoLog stack
(engine + proxy + PostgreSQL). Writes in any region are published to
Kafka, consumed by all regions, and applied locally. Conflict resolution
uses last-writer-wins (LWW) with logical timestamps.

## Alternatives Considered

### Alternative 1: Active-Passive with Failover

- **Pros:** Simple to implement. One primary region handles all writes.
  Secondary regions are read-only replicas.
- **Cons:** Failover takes 30-60 seconds. During failover, all tool call
  interceptions are blocked. The primary region is a single point of
  failure for writes. Read replicas introduce replication lag.
- **Chosen?** No. Does not meet the availability requirement.

### Alternative 2: Active-Active with Kafka (Chosen)

- **Pros:** Every region handles reads and writes locally. Kafka provides
  durable, ordered event delivery across regions. Conflict resolution is
  deterministic (LWW). No single point of failure.
- **Cons:** Kafka adds operational complexity. Cross-region Kafka latency
  (50-150ms) adds to write propagation. LWW can lose writes if two
  regions update the same effect concurrently (acceptable for UndoLog's
  use case).
- **Chosen?** Yes. Matches the availability and latency requirements.

### Alternative 3: Active-Active with CockroachDB

- **Pros:** CockroachDB provides native multi-region active-active with
  serializable isolation. No application-level conflict resolution needed.
- **Cons:** CockroachDB is a significant operational dependency. Licensing
  costs are high for small deployments. The current PostgreSQL advisory
  lock semantics do not translate directly to CockroachDB.
- **Chosen?** No. Too heavy for the current stage. Revisit when
  CockroachDB adoption matures.

## Consequences

**Positive:** Global users get sub-50ms latency for reads (intercept
replay, approval status). Regional outages do not affect other regions.
Write availability is maintained during single-region failures.

**Negative:** Kafka cluster must be deployed and operated. Cross-region
event propagation introduces eventual consistency (typically < 500ms).
Conflict resolution via LWW means concurrent writes to the same effect
may lose one write (acceptable: effect logs are append-only, and the
losing write is logged for audit).

**Risks:** Kafka partition outage can isolate regions. Mitigation: use
multi-AZ Kafka clusters and monitor consumer lag. Clock skew between
regions can affect LWW. Mitigation: use Kafka message timestamps, not
wall clocks.

## References

- [Multi-region strategy design](../design/multi-region-strategy.md):
  Kafka WAL, conflict resolution, cost estimate.
- [Effect states reference](../reference/effect-states.md): state
  machine transitions.
- [PostgreSQL advisory locks](0004-postgresql-advisory-locks.md):
  current single-region deduplication approach.
