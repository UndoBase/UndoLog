---
title: "Saga Pattern: LIFO Compensation Ordering"
description: "## The Problem"
section: "explanation"
---
# Saga Pattern: LIFO Compensation Ordering

## The Problem

An agent onboarding a new customer executes three steps:

1. `create_user` in the identity provider (IdP): succeeds
2. `assign_permissions` in the authorization service: succeeds
3. `send_welcome_email` via the email provider: fails (SMTP timeout)

The agent is in an inconsistent state. The user exists in the IdP but has
no permissions. The welcome email was never sent. A manual operator must
clean up: delete the user, revoke the partial permissions, and decide
whether to retry the email. This cleanup is error-prone and does not scale
to hundreds of agent runs.

The core problem is dependency ordering. Step 2 depends on step 1 (you
cannot assign permissions to a user that does not exist). Step 3 depends
on step 2 (sending an email to a user without permissions is premature).
When step 3 fails, the compensations must run in reverse order: cancel
the email first, then revoke permissions, then delete the user. Running
them in the original order would fail: you cannot delete the user before
removing their permissions (the IdP would reject it).

## The Naive Approach

Try to run compensations in insertion order (FIFO). Delete the user,
then revoke permissions, then cancel the email. This fails at step 1
because the authorization service still references the deleted user. The
compensation itself fails, and the system is worse off than before .
the user is partially deleted and the permissions are orphaned.

Alternatively, run all compensations in parallel. This assumes
compensations are independent, which is false when step B depends on
step A's side effect. Parallel compensation of dependent steps produces
race conditions and inconsistent states.

Some saga implementations require the developer to manually specify
compensation ordering. This is brittle: adding a new step in the future
requires updating the ordering configuration, and there is no compile-time
check that the ordering is correct.

## The UndoLog Approach: LIFO Compensation Stack

UndoLog implements a saga pattern using a LIFO (Last-In-First-Out)
compensation stack per session. Each time a compensable tool call is
intercepted, its compensation descriptor is pushed onto the undo stack
with a monotonically increasing `stack_position`. When a session fails,
the `SagaOrchestrator` pops entries in descending `stack_position` order: the most recently pushed compensation runs first.

### Registration Before Execution (Critical Invariant)

The compensation is always registered in `undolog_undo_stack` **before**
the action executes. The `registered_at` timestamp precedes the
`executed_at` timestamp in `undolog_effect_log`. This ordering is not
an optimization: it is a correctness guarantee.

If the process crashes between registration and execution, the
compensation is already persisted. On recovery, the orchestrator finds
a pending undo entry for an uncommitted effect. It runs the compensation
because the effect was never committed: the safest default is to undo
an action whose outcome is unknown.

If the process crashes before registration, the action was never
intercepted, no effect log entry exists, and no compensation is needed.
The crash is indistinguishable from the call never being made.

This invariant is enforced at two levels:
- Application logic: the `EffectEngine.intercept` method pushes the undo
  entry before returning `Execute` to the proxy
- Database: `undolog_undo_stack.registered_at` defaults to `now()` and
  is set during the same transaction as the effect log insert

### Stack Structure and Ordering

Each undo entry carries:

```
UndoEntry {
    undo_id:          UndoId,        // unique identifier
    org_id:           OrgId,         // tenant
    session_id:       SessionId,     // owning session
    effect_id:        EffectId,      // the effect being compensated
    stack_position:   u32,           // LIFO order: higher = later = compensated first
    compensation:     CompensationDescriptor,  // fn_name, version, args, retry config
    state:            SagaStepState, // pending | running | compensated | failed | skipped
    retry_count:      u8,
    last_error:       Option<String>,
    registered_at:    DateTime<Utc>, // MUST precede action execution
    compensated_at:   Option<DateTime<Utc>>,
}
```

`stack_position` is set to the tool call's `step_index`. Since step
indices are monotonically increasing within a session, loading with
`ORDER BY stack_position DESC` produces the correct LIFO order. The
UNIQUE constraint `(session_id, stack_position)` prevents accidental
duplicates.

### The SagaOrchestrator

When a session fails (tool execution error, network timeout, or
workflow-level exception), the `SagaOrchestrator.compensate_session`
method is invoked:

```
1. Load session record: verify it is not already terminal
2. Load undo stack. SELECT ... WHERE state = 'pending' ORDER BY stack_position DESC
3. For each entry:
   a. Resolve compensation endpoint from CompensationRegistry
   b. Call the compensation endpoint with:
      - HTTP POST to the resolved URL
      - Body: the original compensation args (captured before action)
      - Header: Idempotency-Key: undo-{undo_id}
   c. On success: mark entry as compensated
   d. On failure (all retries exhausted): mark session as halted, stop
4. If all entries compensated: mark session as compensated
```

See `crates/undolog-saga/src/orchestrator.rs:240-314` for the full
implementation.

### Compensation Runner with Retry

Each compensation call is executed by `compensation_runner::execute_entry`
with:
- **Idempotency key**: `Idempotency-Key: undo-{undo_id}`: ensures the
  compensation endpoint can safely retry without double-effect
- **Exponential backoff with jitter**: 100ms initial interval, 2x
  multiplier, 5s max, 3 retries by default (configurable per compensation
  via `CompensationDescriptor.max_retries`)
- **Permanent failure detection**: HTTP 4xx (except 429 Too Many Requests)
  is treated as permanent: no retry, immediate escalation to `halted`

The compensation endpoint must be idempotent with respect to the
`Idempotency-Key` header. The framework does not enforce this, it is a
contract the compensation implementer must fulfill. The idempotency key
format is `undo-{undo_id}` where `undo_id` is the UUID of the undo stack
entry.

### State Machine

The session and effect state machines interact:

```
Session                    Effect (per tool call)
─────────────────────      ─────────────────────
active                     pending → executing → committed
  ↓ (failure)              
failed                      
  ↓                         
compensating               pending → compensating → compensated
  ↓ (all compensated)       pending → compensating → compensation_failed → (halted)
compensated                 
                            
  ↓ (compensation fails)    
halted                     
```

When a compensation fails permanently (all retries exhausted), the
session enters `halted`. The remaining undo entries are left in
`pending` state. A human must intervene: no automated retry is
attempted because the compensation itself is broken. The `halted`
session and its pending undo entries are surfaced in the dashboard
for manual resolution.

### Crash Recovery

On process restart, the engine scans for sessions in `compensating` or
`failed` state with pending undo stack entries. These sessions are
resumed by the `SagaOrchestrator`, which re-loads the undo stack and
continues from the first un-compensated entry.

Because each compensation carries an idempotency key, re-executing a
previously partially-compensated entry is safe: the upstream endpoint
returns the cached result (or no-ops) on duplicate idempotency key.

## Trade-offs

**LIFO assumes compensations are independent of each other.** The LIFO
ordering guarantees that a compensation runs after all steps that depend
on it have been compensated. But it does not help if two compensations
must run in parallel for performance reasons. Parallel compensation
requires additional orchestration logic outside UndoLog.

**Pre-registration adds latency.** Every compensable tool call pays for
two additional database writes (effect log insert + undo stack push)
before execution. This is acceptable for agent workloads where tool
calls are measured in seconds, not microseconds.

**Compensation code is production code.** A bug in a compensation
function (wrong args, missing idempotency, non-terminating retry)
escalates the session to `halted`. Teams must test compensation paths
as rigorously as the forward paths.

**Idempotency is a contract, not enforcement.** UndoLog sends the
`Idempotency-Key` header but cannot verify that the upstream endpoint
honors it. A non-idempotent compensation endpoint can produce
double-effects even with the correct key.

## Alternatives Considered

**FIFO compensation (insertion order).** Walk the undo stack in the
direction it was built. Fails when step n+1 depends on step n because
you cannot remove the dependency before the dependent. Rejected because
dependency ordering is the norm in agent workflows.

**Parallel compensation.** Run all compensations concurrently. Fast but
unsafe when compensations share resources. Rejected because it requires
the developer to prove independence, which is unenforceable.

**Manual saga definition.** The developer explicitly defines compensation
order in a saga configuration file. Flexible but brittle: adding a step
requires updating the saga definition. Rejected because it places the
burden of correctness on the developer rather than the framework.

**Choreography (event-driven sagas).** Each service publishes
compensation events consumed by downstream services. Decoupled but
impossible to reason about ordering globally. Rejected because agent
sessions are short-lived and benefit from centralized orchestration.

## Further Reading

- [Safety Model](./safety-model.md): how Compensable tier triggers
  saga orchestration
- [Exactly-Once Semantics](./exactly-once-semantics.md): idempotency
  keys in compensation HTTP calls
- Code: `crates/undolog-saga/src/orchestrator.rs:240-314` .
  SagaOrchestrator.compensate_session
- Code: `crates/undolog-saga/src/compensation_runner.rs:45-166` .
  exponential backoff with retry
- Code: `crates/undolog-types/src/saga.rs:1-76`. UndoEntry and
  SagaStepState types
- Schema: `migrations/0001_initial.sql:467-511`: undolog_undo_stack
  table and LIFO index
