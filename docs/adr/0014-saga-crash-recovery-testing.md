---
title: "ADR 0014: Saga Crash Recovery Testing Approach"
description: "- **Date:** 2026-09-17 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0014: Saga Crash Recovery Testing Approach

- **Date:** 2026-09-17
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

Saga crash recovery is a core reliability guarantee. When the engine
process dies mid-compensation, the orchestrator must resume from where
it left off. Testing this requires killing the engine process at
precise points and verifying recovery behavior.

Current state:

- Integration tests cover normal compensation flow
- No tests for process crash during compensation
- No test infrastructure for process lifecycle management
- No CI integration for crash recovery scenarios

## Decision

Crash recovery tests use a separate engine process managed by the
test harness. The test harness controls the process lifecycle:
start, inject faults, kill, restart, and verify state. Tests run
in CI with process isolation to prevent interference with other tests.

## Alternatives Considered

### Alternative 1: Process Lifecycle Management (Chosen)

- **Pros:** Tests real crash scenarios. Process isolation prevents
  interference. CI integration is straightforward.
- **Cons:** Requires process management in test harness. Slower than
  in-process tests. May need port allocation for test servers.
- **Chosen?** Yes. Matches the project's priority: reliability through
  real-world testing.

### Alternative 2: In-Process Fault Injection

- **Pros:** Faster execution. No process management overhead.
- **Cons:** Does not test real crash scenarios. May miss process-level
  recovery issues. Harder to simulate abrupt termination.
- **Chosen?** No. Does not validate actual crash recovery behavior.

### Alternative 3: Mock-Based Recovery Testing

- **Pros:** Fastest execution. Full control over failure scenarios.
- **Cons:** Does not test actual recovery logic. May miss storage
  consistency issues. Does not validate process restart behavior.
- **Chosen?** No. Does not provide real confidence in crash recovery.

## Consequences

**Positive:** Crash recovery is validated with real process lifecycle.
CI catches regressions in recovery logic. Test isolation prevents
flaky tests. Process management infrastructure is reusable for other
integration tests.

**Negative:** Tests are slower than in-process alternatives. Process
management adds complexity to test harness. Port allocation may
cause conflicts in parallel test execution.

**Risks:** Process management may be flaky on CI runners. Mitigation:
use fixed ports with retry logic for port binding. Test isolation
may not be perfect. Mitigation: use unique session IDs per test.

## References

- [Saga orchestrator](../../crates/undolog-saga/src/orchestrator.rs):
  compensation execution logic
- [Integration tests](../../crates/undolog-saga/tests/): existing
  test patterns
- [Failure mode runbook](../runbooks/failure-modes.md): crash
  recovery scenarios
