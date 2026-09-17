---
title: "ADR 0016: Effect TTL and Retention Strategy"
description: "- **Date:** 2026-09-17 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0016: Effect TTL and Retention Strategy

- **Date:** 2026-09-17
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

The effect log is monthly-partitioned but has no archival or deletion
mechanism. Over time this becomes a storage and query-performance
problem. When the effect log reaches 1 billion rows, the UNIQUE
constraint on `call_signature` may not perform well.

The failure mode is gradual degradation (storage growth), not silent
corruption. The risk is real but the timeline is months, not days.

Current state:

- Effect log is append-only with monthly partitions
- No TTL or retention policy exists
- No automated partition management
- No archival to cold storage

## Decision

Effect log entries are retained for a configurable period (default: 90
days) per organisation. Old partitions are dropped after optional
archival to cold storage (S3/Blob). Partition management is automated:
new partitions are created proactively, old ones are dropped after
retention expires.

## Alternatives Considered

### Alternative 1: Per-Org Configurable TTL with Automated Partitions (Chosen)

- **Pros:** Organisations control their own retention. Automated
  partition management reduces operational burden. Archival before
  deletion preserves data for compliance. Configurable via environment
  variable or config file.
- **Cons:** Two code paths (retention policy + partition management).
  Must be tested with varying retention periods. Archival adds S3
  dependency (optional).
- **Chosen?** Yes. Matches the project's priority: operational safety
  with configurability.

### Alternative 2: Fixed Global TTL

- **Pros:** Simple to implement. One retention period for all orgs.
- **Cons:** Does not meet the per-org configurability requirement.
  Some orgs need 30-day retention, others need 365 days.
- **Chosen?** No. Too rigid for multi-tenant deployments.

### Alternative 3: No TTL, Manual Archival Only

- **Pros:** No automated deletion. Full control over what is archived.
- **Cons:** Storage grows unbounded. Operational burden is high.
  Risk of out-of-storage incidents.
- **Chosen?** No. Does not address the storage growth problem.

## Consequences

**Positive:** Storage growth is bounded. Per-org configurability meets
compliance requirements. Automated partition management reduces
operational burden. Archival preserves data for audit trails.

**Negative:** Retention policy logic must be tested. Partition
management must handle edge cases (concurrent drops, failed archives).
Metrics must be added to monitor partition health.

**Risks:** Retention policy may delete data before orgs are ready.
Mitigation: default to 90 days, require explicit opt-in for shorter
periods. Archival to S3 may fail silently. Mitigation: log archival
failures and alert.

## References

- [Effect states reference](../reference/effect-states.md): state
  machine transitions
- [Database schema](../reference/database-schema.md): partition
  structure
