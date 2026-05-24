---
title: "Safety Model: Three-Tier Tool Classification"
description: "## The Problem"
section: "explanation"
---
# Safety Model: Three-Tier Tool Classification

## The Problem

A customer-support agent built on LangGraph has access to three tools:
`search_knowledge_base`, `send_refund_email`, and `delete_user_account`. An
automated test calls `delete_user_account` during a CI run because the LLM
misinterpreted a prompt. The account is gone. There is no undo.

This is not a theoretical risk. Real-world incidents involving AI agents
executing destructive tool calls are well-documented. The root cause is
always the same: every tool call receives equal treatment. A RAG search
that reads a vector store and a production database deletion flow through
the same code path. No system distinguishes between "this call is harmless"
and "this call requires a human in the loop."

Most agent frameworks (LangGraph, CrewAI, Semantic Kernel) provide no
native safety classification. They treat the LLM's tool choice as
authoritative and execute immediately. Safety, if it exists, is bolted on
as middleware after the fact: a prompt engineering pattern telling the LLM
"be careful" rather than an architectural guard.

## The Naive Approach

The obvious fix is a single "confirm before destructive" flag on every tool
call. Every invocation, even `list_files`, pauses for confirmation. Users
become blind to confirmation dialogs and click through automatically.
Alternatively, a two-tier system (safe / destructive) creates a binary
choice where "destructive" lumps together operations with fundamentally
different risk profiles: sending a notification email and deleting a
production database are not the same class of action, but a two-tier system
treats them identically.

Neither approach provides a mechanism for automated recovery. A two-tier
system that blocks destructive calls with no undo capability leaves the
agent stuck. The only option is manual cleanup, which defeats the purpose
of automation.

## The UndoLog Approach: Three Tiers

UndoLog classifies every tool into exactly one of three tiers at
registration time. Classification is declarative: an SDK annotation on
the tool definition: never inferred by the LLM or derived from runtime
analysis. The tier determines how the interception engine routes the call.

### SAFE: Execute freely, no tracking

Safe tools are read-only or idempotent operations. The interception engine
bypasses the effect log entirely. No database write, no undo stack entry,
no performance overhead.

```
@undolog_tool(tier=ToolTier.SAFE)
async def search_knowledge_base(query: str) -> list[Result]:
    return await vector_store.search(query)
```

Examples: `search_web`, `read_file`, `get_user`, `list_records`.

The engine returns `Execute { effect_id }`: a lightweight handle, and
the proxy forwards the call to the upstream tool server immediately. There
is no compensation and no approval gate. The effect log records nothing
for safe calls; the only trace is the session's tool_calls_total counter
(see `undolog_sessions`).

This tier exists because logging every read incurs unnecessary database
overhead and pollutes the effect log with noise. A typical agent session
may include 20+ search calls for every write call. Tracking all of them
would multiply storage costs and slow the hot path with no safety benefit.

### COMPENSABLE: Execute with a parachute

Compensable tools are write operations that have a well-defined undo
function: a compensation. Before the tool executes, the engine:
1. Inserts a row into `undolog_effect_log` with state `pending`
2. Pushes a compensation descriptor onto `undolog_undo_stack`

Both operations complete before execution begins. The `undolog_undo_stack`
entry captures the compensation function name, version, and argument
snapshot at registration time: before the action mutates state. This
ordering is critical: if the process crashes between the undo stack insert
and the tool execution, the compensation is already persisted and will be
re-attempted on recovery.

```
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("cancel_refund_email"),
)
async def send_refund_email(user_id: str, amount: decimal.Decimal) -> dict:
    return await email_service.send(user_id, amount)
```

After successful execution, the engine transitions the effect state from
`executing` to `committed` and caches the result in `result_snapshot`. On
failure, the `SagaOrchestrator` walks the undo stack in LIFO order,
calling each compensation with an `Idempotency-Key: undo-{undo_id}` header
(see [Saga Pattern](./saga-pattern.md)).

### IRREVERSIBLE: Human gate

Irreversible tools perform actions that cannot be undone. When the engine
intercepts an irreversible call, it:
1. Inserts into `undolog_effect_log` with state `pending`
2. Creates an `undolog_approval_requests` row with the proposed arguments,
   irreversibility reason, risk tags, and agent context
3. Suspends the session: transitions `undolog_sessions.state` to
   `awaiting_approval`

```
@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_user_account(user_id: str) -> dict:
    return await account_service.delete(user_id)
```

The engine returns `AwaitingApproval { effect_id, approval_request_id }`.
The proxy responds to the agent with HTTP 202 Accepted and a `retry_after`
hint. The agent must poll or wait for a webhook notification before
proceeding (see `undolog-engine/src/engine.rs:217-284`).

A human reviews the request through the dashboard or REST API. The
dashboard shows the proposed arguments, the irreversibility reason, the
last N tool calls from the session for context, and any risk tags. The
approver can:
- **Approve**: the effect transitions to `approved`, the session resumes
  to `active`, and the tool executes
- **Reject**: the effect transitions to `rejected`, the session enters
  `halted`
- **Modify**: the approver edits the arguments before approving (logged in
  `undolog_approval_events` with action `modify`)

Approval requests carry a `timeout_at` timestamp. If the timeout expires,
the request transitions to `timed_out` (or `auto_approved` if the org
policy sets `auto_approve_on_timeout = true`). Every action: approve,
reject, modify, timeout: is recorded immutably in
`undolog_approval_events` for audit compliance.

## Trade-offs

**SAFE** gives maximum execution speed with zero overhead. The cost is no
safety net: if a tool is misclassified as safe when it should be
compensable or irreversible, the system provides no protection. The
classification decision is a single point of correctness.

**COMPENSABLE** provides full automated recovery but requires writing and
maintaining compensation functions. Each compensation must be idempotent
and handle partial-failure states. The compensation code is production
code: if the compensation itself has a bug, the session enters `halted`
and requires manual intervention. The pre-execution logging also adds
latency: two database writes (effect log + undo stack) precede every
compensable tool call.

**IRREVERSIBLE** provides the strongest safety guarantee: a human always
reviews before execution: but introduces unpredictable latency. An agent
may stall for hours waiting for approval if the human is unavailable. The
approval gate also increases operational complexity: teams need a rotation
of approvers, notification policies, and timeout escalation rules.

## Alternatives Considered

**No classification (LangGraph default).** Every tool call is fire and
forget. Simple to implement but provides no safety guarantees. Rejected
because the entire purpose of UndoLog is to provide a safety layer that
the agent framework does not.

**Two-tier (safe / destructive).** A single binary classification reduces
the design surface but loses the distinction between undoable and
non-undoable writes. A compensation-capable system requires the three-way
split because the compensation machinery (undo stack push, descriptor
capture) is only meaningful for operations that actually have a defined
undo. Rejected because it conflates recoverable and unrecoverable actions.

**LLM-inferred classification.** Ask the LLM to classify its own tool
calls at runtime based on the tool description. This approach has been
tried in production (see related work) and fails because LLMs are
inconsistent classifiers: the same tool call may be classified as safe
in one invocation and destructive in another, depending on prompt
structure. UndoLog's classification is declarative and immutable,
enforced at the database level via `CHECK` constraints on
`undolog_tool_registry`.

## Further Reading

- [Exactly-Once Semantics](./exactly-once-semantics.md): how the effect
  log prevents duplicate execution
- [Saga Pattern](./saga-pattern.md). LIFO compensation ordering and
  crash recovery
- [MCP-Native Design](./mcp-native-design.md): why tier classification
  lives at the MCP protocol layer
- Code: `crates/undolog-types/src/tier.rs:9-33`. ToolTier enum with
  Safe, Compensable, Irreversible variants
- Code: `crates/undolog-engine/src/engine.rs:112-286`: intercept method
  with per-tier dispatch
- Schema: `migrations/0001_initial.sql:72-113`: enum definitions and
  CHECK constraints
