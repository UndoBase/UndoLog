---
title: "ADR 0013: Dead-Letter Queue for Failed Compensations"
description: "- **Date:** 2026-09-17 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0013: Dead-Letter Queue for Failed Compensations

- **Date:** 2026-09-17
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

When a compensation exhausts its retries, the saga is stuck in
`compensation_failed` with no escalation path. There is no way to
inspect, retry, or skip it. The failed compensation blocks the session
and requires manual database intervention to resolve.

Current state:

- Compensation retries are hardcoded (default: 3)
- After exhaustion, effect enters `compensation_failed` terminal state
- No admin API to inspect or retry failed compensations
- No visibility into compensation failure rates

## Decision

Failed compensations are moved to a dead-letter table
(`undolog_dead_letters`) after exhausting retries. An admin API
provides endpoints to inspect, retry, or skip dead letters. The
dead-letter record includes full context: session ID, effect ID,
compensation descriptor, error, retry count, and timestamps.

## Alternatives Considered

### Alternative 1: Dead-Letter Table with Admin API (Chosen)

- **Pros:** Full visibility into failed compensations. Retry and skip
  operations are explicit. Admin API integrates with dashboards.
  Dead-letter records preserve audit trail.
- **Cons:** New table and API to maintain. Requires admin
  authentication. Dead-letter records grow over time (mitigated by
  retention policy).
- **Chosen?** Yes. Matches the project's priority: operational safety
  with visibility.

### Alternative 2: Log and Forget

- **Pros:** Simple to implement. No new tables or APIs.
- **Cons:** No visibility into failures. No retry mechanism. Manual
  database intervention required.
- **Chosen?** No. Does not provide an escalation path.

### Alternative 3: Automatic Retry with Exponential Backoff

- **Pros:** Retries automatically without admin intervention.
- **Cons:** May retry indefinitely if the underlying issue is permanent.
  No visibility into failure patterns. May overwhelm downstream
  services during outages.
- **Chosen?** No. Requires bounded retries with explicit escalation.

## Consequences

**Positive:** Failed compensations are visible and manageable. Admin
API integrates with monitoring dashboards. Retry and skip operations
are explicit and auditable. Dead-letter records preserve full context
for debugging.

**Negative:** New table and API to maintain. Admin authentication
required. Dead-letter records grow over time (mitigated by retention
policy). Must handle edge cases: concurrent retries, skip during retry.

**Risks:** Dead-letter table may grow unbounded if not retained.
Mitigation: apply TTL retention policy to dead-letter records. Admin
API may be exploited if authentication is weak. Mitigation: enforce
strong authentication and rate limiting.

## References

- [Effect states reference](../reference/effect-states.md): state
  machine transitions
- [Error codes reference](../reference/error-codes.md):
  `CompensationFailed` error
- [Failure mode runbook](../runbooks/failure-modes.md): compensation
  failure handling
