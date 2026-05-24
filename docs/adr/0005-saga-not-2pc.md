---
title: "ADR 0005: Saga Pattern for Multi-Step Rollback (Not 2PC)"
description: "- **Date:** 2026-02-12 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0005: Saga Pattern for Multi-Step Rollback (Not 2PC)

- **Date:** 2026-02-12
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

An agent workflow often involves multiple sequential tool calls: for example, `create_draft`, `send_email`, `archive_draft`. If `send_email` fails, the agent should undo the `create_draft` effect. These effects span external APIs that UndoLog does not control. REST endpoints, third-party SaaS services, and database mutations. The rollback mechanism must work across this heterogeneous landscape without requiring all participants to support a common transaction protocol.

## Decision

Use the Saga pattern with a LIFO (last-in, first-out) compensation stack instead of two-phase commit (2PC).

## Alternatives Considered

### Two-Phase Commit (2PC)

- **Pros:** Strong atomicity guarantees: either all participants commit or all abort. ACID properties hold across distributed participants if they all support the protocol. Well-understood in database systems.
- **Cons:** Requires all participants to implement a prepare/commit/abort protocol. Most HTTP APIs and SaaS services do not support 2PC. The coordinator holds locks and resources during the prepare phase, increasing contention windows. The coordinator is a single point of failure, if it crashes during the commit phase, participants remain in-doubt indefinitely.
- **Chosen?** No. The requirement to support arbitrary HTTP APIs makes 2PC infeasible. UndoLog cannot require every tool's underlying service to implement XA or similar protocols.

### Orchestrated Saga with Compensation (Chosen Pattern)

- **Pros:** Works with any HTTP API: the compensation is simply another API call. Compensations are independently testable and can be verified without running the full saga. No need for participants to be aware of the transaction protocol.
- **Cons:** Weaker guarantees than 2PC: the system is eventually consistent. There is a window between a failure and the completion of all compensations. Compensations themselves can fail, requiring retry logic and potentially dead-letter handling.
- **Chosen?** Yes. The pragmatic ability to work with any API type outweighs the weaker consistency guarantees. Compensations are implemented as reversible actions (e.g., `delete_draft` reverses `create_draft`), which the service author provides.

### Eventual Consistency with Periodic Reconciliation

- **Pros:** Simplest implementation: no compensation logic needed. A reconciliation job detects and fixes inconsistencies on a schedule.
- **Cons:** Leaves the system in an inconsistent state for the entire reconciliation window. May amplify damage (e.g., an email sent but not undone for minutes). Requires idempotency and convergence logic that is harder to verify than explicit compensations.
- **Chosen?** No. The gap between failure and reconciliation is unacceptably long for UndoLog's safety guarantees.

## Consequences

**Positive:** Works with any HTTP API regardless of transaction protocol support. Compensations are independently testable, each compensation endpoint can be unit-tested without running the full saga. No long-held locks or resources (compensations execute sequentially). The LIFO order ensures dependent effects are undone before their dependencies (e.g., delete the email before removing the draft).

**Negative:** Eventual consistency: there is a gap between the failure detection and the completion of the full compensation chain. Compensations can fail (network error, API outage), requiring retry with exponential backoff and a dead-letter queue for unrecoverable failures.

**Risks:** If a compensation is not written correctly (e.g., it tries to delete a resource that was already deleted by another path), the compensation itself may cause errors. The saga orchestrator must handle partial failures during compensation, if compensation for step 3 fails, compensations for steps 2 and 1 should still proceed.
