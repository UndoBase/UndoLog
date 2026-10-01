---
title: "ADR 0017: Compensation Testing Harness for the Python SDK"
description: "- **Date:** 2026-09-30 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0017: Compensation Testing Harness for the Python SDK

- **Date:** 2026-09-30
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

Compensation functions are production code with a strict contract: they
must be idempotent (the engine re-attempts them after a crash), they
must tolerate retries with backoff, and they run in LIFO order within a
session. Despite carrying this contract, they are rarely tested as
such. A developer who writes `undo_send_email` typically exercises it
only through the full stack, which requires Docker, PostgreSQL, the
engine, the proxy, and a manually induced failure. This friction means
the idempotency and retry guarantees are usually untested.

Current state:

- `sdks/undolog-py/undolog_sdk/test_harness.py` provides
  `CompensationTestHarness`: it wraps one async compensation function
  and records call counts, arguments, results, and errors, with retry
  and idempotency assertion helpers.
- The harness is already exported in the package `__all__`.
- There is no session-level harness: no way to simulate a whole
  compensation chain in LIFO order, no replay of a recorded session's
  effects, and no report of per-step outcomes.
- The class is importable from the package root, but the planned
  top-level entry points (`undolog.test_compensation(fn, args)` and
  `undolog.test_saga(session_effects)`) do not exist yet.

The engine side of this boundary is tracked separately as EN-7
(compensation interface in `plan/complete/engine-saga.md`). This ADR
covers only what the SDK can do without a running engine.

## Decision

The SDK grows a test-only surface with two layers, both living in
`undolog_sdk.test_harness` and both working in plain `pytest` runs with
no engine, no proxy, and no database.

**Layer 1 (exists today): `CompensationTestHarness`.** Wraps a single
compensation function. `execute()` records every invocation;
`execute_with_retry()` models the orchestrator's retry loop against
`max_retries`; `assert_idempotent()` executes twice with identical
arguments and fails when the results differ.

**Layer 2 (to build): `SagaTestHarness` and top-level entry points.**

- `SagaTestHarness(session_effects)` accepts a list of recorded effect
  descriptors, each carrying the tool name and its compensation
  callable.
- `rollback()` simulates the orchestrator's compensation pass: entries
  execute in LIFO order by `stack_position`, each one is retried up to
  its `max_retries`, already-terminal entries are skipped (mirroring
  the orchestrator's resume behavior), and execution stops on the
  first permanently failed entry (the engine's fail-fast rule, which
  halts the session). Following the engine contract, a `max_retries`
  of 0 selects the default retry budget rather than disabling retries,
  and permanent client errors are not retried.
- `report()` returns per-step outcomes (tool name, stack position,
  attempts made, final state) and an overall session result that
  mirrors the engine's `SessionState`: `compensated` when every entry
  compensates, `halted` when one fails permanently.

Two module-level convenience functions wrap the classes:

- `test_compensation(fn, args)` runs a single compensation through the
  Layer 1 harness, verifies the idempotency contract by invoking it a
  second time with identical arguments, and returns a result object
  with the execution count, retry count, final state, and return
  value.
- `test_saga(session_effects)` runs a full LIFO chain through the
  Layer 2 harness and returns the same style of report.

Layer 1 is exported from `undolog_sdk` today; when Layer 2 lands, both
classes and both functions will be exported from the package root.
The harnesses simulate, they do not mock the engine: they apply the
orchestrator's rollback rules (LIFO order, per-entry retry budgets,
fail-fast on permanent failure) to plain Python callables. The
separate engine invariant that compensations are pre-registered before
execution is context for why recorded effects exist, not something the
harness re-enforces.

## Alternatives Considered

### Alternative 1: Two-Layer In-Process Harness on the Existing Base (Chosen)

- **Pros:** No engine, no database, runs in milliseconds inside any CI
  job. Reuses the already-shipped `CompensationTestHarness`, so Layer 2
  is the only new code. Errors surface as ordinary Python exceptions,
  so failure output reads like any other unit test failure.
- **Cons:** It validates the compensation functions against the
  documented contract, not against the live engine. Drift between the
  orchestrator's real behavior and the harness's model would go
  unnoticed by these tests alone.
- **Chosen?** Yes. The purpose of this harness is fast feedback on
  compensation code in isolation. Fidelity to the live orchestrator is
  covered by the saga integration suite (crash recovery tests in
  `crates/undolog-saga`).

### Alternative 2: Test Against a Real Engine in Docker

- **Pros:** Highest fidelity: the exact production code paths run.
- **Cons:** Requires the full stack for what should be a unit test.
  Slow, order-dependent, and awkward to induce a compensation failure
  deterministically. Reinforces the exact friction this ADR removes.
- **Chosen?** No. This is the integration tier, which already exists;
  it does not replace a unit-level harness.

### Alternative 3: Mock the Engine gRPC Client and Drive the Real Orchestrator

- **Pros:** Exercises the real orchestrator logic in-process.
- **Cons:** Rust orchestrator, Python test: would need a
  Python-to-Rust bridge (PyO3) or the proxy in the loop, both heavy
  new dependencies for a test utility, violating the zero-unnecessary-
  dependencies principle. Also couples SDK tests to engine internals.
- **Chosen?** No. Cost and coupling outweigh the benefit for an
  SDK-side utility.

### Alternative 4: Do Nothing, Rely on Integration Tests

- **Pros:** No new surface to maintain.
- **Cons:** Compensation contract violations (non-idempotent
  compensations, retry-busting exceptions) are found late, in
  full-stack runs, or in production. The friction documented in the
  Context section remains.
- **Chosen?** No. The contract is testable cheaply; not testing it is
  the status quo problem.

## Consequences

**Positive:**

- Compensation functions get a standard, fast way to verify idempotency
  and retry behavior in CI without infrastructure.
- Session-level rollback order (LIFO, fail-fast) becomes testable from
  the SDK side before EN-7 lands.
- The top-level `test_compensation` / `test_saga` names match the
  acceptance criteria in PY-4 and EN-7, keeping plan and API aligned.
- Pure standard library; no new dependencies.

**Negative:**

- The harness encodes a model of the orchestrator's rules; if the
  engine changes its rollback semantics, the model must be updated in
  lockstep. Mitigation: the crash-recovery integration tests remain the
  source of truth for engine behavior.
- Two ways to test compensations (unit harness, live stack) require a
  short documentation note on when to use which.

**Risks:**

- Users may treat a passing harness run as proof of engine-level
  correctness. Mitigation: docstrings and the migration note state
  explicitly that the harness validates the compensation function's
  contract, not the orchestrator.
- `SagaTestHarness` must not silently reorder entries with equal
  `stack_position`. Mitigation: reject duplicate `stack_position`
  values at construction time with a clear error.
- The existing `execute_with_retry` treats every exception as
  retryable and applies no backoff, a simplification of the engine's
  permanent-versus-retryable classification. Mitigation: document the
  simplification on the method and align it with the engine contract
  when Layer 2 lands.

## References

- Existing harness: `sdks/undolog-py/undolog_sdk/test_harness.py`
- Engine undo stack entry (`UndoEntry`, LIFO `stack_position`,
  pre-registered `registered_at`): `crates/undolog-types/src/saga.rs`
- Saga orchestrator: `crates/undolog-saga`
- Engine-side counterpart: EN-7 in `plan/complete/engine-saga.md`
- SDK plan item: PY-4 in `plan/python-sdk.md`
- ADR 0007: Pre-Registered Compensation for Crash Safety
