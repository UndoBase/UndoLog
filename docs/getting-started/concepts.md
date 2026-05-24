---
title: "Core concepts"
description: "UndoLog protects your AI agent by classifying every tool call into one of three tiers and recording every non-safe call into an immutable **effect journal**. When something goes wrong, the system walk"
section: "getting-started"
---
# Core concepts

UndoLog protects your AI agent by classifying every tool call into one of three
tiers and recording every non-safe call into an immutable **effect journal**.
When something goes wrong, the system walks a pre-registered **undo stack** to
reverse compensable actions and suspends irreversible ones until a human
decides.

---

## Three-tier safety model

Every tool carries a tier annotation at decoration time. The agent never
chooses the tier: you declare it when you write the tool.

```python
@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str: ...

@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=...)
async def send_email(to: str) -> dict: ...

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_database(name: str) -> dict: ...
```

| | SAFE | COMPENSABLE | IRREVERSIBLE |
|---|---|---|---|
| Effect log entry | No | Yes | Yes |
| Compensation registered | No | Before execution | No |
| Human approval gate | No | No | Yes, before execution |
| Replayable from cache | No | Yes | Yes (after commit) |
| On session failure | Nothing | Engine walks undo stack LIFO | Session stays halted |
| Examples | `search_web`, `read_file` | `send_email`, `create_user` | `delete_db`, `process_payout` |

**SAFE** tools bypass the proxy entirely. The decorator calls your function
directly with zero network overhead. Use this for reads, idempotent lookups,
and any operation that leaves no side-effect trail.

**COMPENSABLE** tools go through the proxy, which logs the call and registers
a compensation on the undo stack *before* your function executes. If the
session later fails, the engine invokes compensations in LIFO order. The
last successful action is the first one undone. The compensation descriptor
(which function to call, with which arguments) is captured at registration
time so it survives a process crash.

**IRREVERSIBLE** tools also go through the proxy, but the engine creates an
approval request and raises `AwaitingApprovalError` instead of executing. The
session enters the `awaiting_approval` state and stays there until a human
approves or rejects the request through the dashboard or API. The tool body
never runs without explicit consent.

---

## Effect journal

The effect journal is an append-only log stored in the `undolog_effect_log`
table. Every intercepted tool call produces one row. Nothing is ever deleted
or updated in place: corrections are new events.

```
undolog_effect_log
─────────────────────────────────────────────────────────────
effect_id  │ call_signature  │ tier  │ args    │ state
───────────┼─────────────────┼───────┼─────────┼────────────
uuid-1     │ abc123...       │ comp  │ {...}   │ committed
uuid-2     │ def456...       │ irr   │ {...}   │ approved
```

**Exactly-once via BLAKE3.** Each row carries a `call_signature`, a 64-char
hex BLAKE3 hash of `(session_id, step_index, tool_name, canonical_args)`.
The column has a unique index. When the proxy tries to insert a duplicate
signature, the `ON CONFLICT DO NOTHING` guard makes it a no-op and the engine
returns the cached `result_snapshot` from the original execution. This gives
you replay safety for free.

**Canonical JSON.** Arguments are hashed in a deterministic form. The
`canonical_json` function sorts all dictionary keys recursively so that
`{"b":1,"a":2}` and `{"a":2,"b":1}` produce the same signature regardless of
language or insertion order.

**Advisory locks.** Before inserting, the engine acquires a
`pg_try_advisory_xact_lock` keyed on the call signature hash. This prevents
two concurrent proxy instances from racing on the same tool call, avoiding
write-conflict rollbacks under high concurrency.

The log is range-partitioned by `executed_at` (monthly). Old partitions can be
dropped in a single DDL statement without affecting the rest of the table.

---

## Compensations

A compensation is a function that reverses the effect of a COMPENSABLE tool.
You declare it at decoration time:

```python
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        "delete_user",
        args={"soft": True},
    ),
)
async def create_user(email: str, name: str) -> dict: ...
```

**Pre-registered before execution.** The proxy writes the compensation into
`undolog_undo_stack` *before* `create_user` runs. If the process crashes
between the registration and the tool execution, the engine still knows that
a compensation exists: and it runs it during recovery.

**LIFO order.** When a session fails, the engine loads the undo stack ordered
by `stack_position DESC` (most recently pushed first). If your agent calls
`create_user` then `send_email`, the engine undoes `send_email` first, then
`create_user`.

**Idempotency.** Every compensation call carries the `undo_id` as an
idempotency key. If the engine crashes mid-compensation and retries, the
compensation endpoint receives the same `undo_id` and can safely no-op.

**Crash recovery.** On startup, the engine scans for sessions in the
`compensating` or `active` state with pending undo-stack entries and resumes
the compensation walk. No manual intervention is required for compensable
actions: only `compensation_failed` entries escalate to human operators.

---

## Session state machine

A session moves through these states over its lifetime:

```
                    ┌────────────────────────────┐
                    │         active             │
                    │  (tool calls flowing)      │
                    └────────┬───────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌────────────┐ ┌───────────────┐ ┌──────────────────┐
     │ completed  │ │  failed       │ │ awaiting_approval│
     │ (success)  │ │ (error)       │ │ (human gate)     │
     └────────────┘ └──────┬────────┘ └────────┬─────────┘
                           │                   │
                           ▼                   │
                    ┌────────────┐             │
                    │compensating│◄────────────┘
                    │ (undo walk)│    (if approval
                    └─────┬──────┘     rejected)
                          │
                    ┌─────▼──────┐         ┌──────────────────┐
                    │ compensated│         │     halted       │
                    │ (all done) │         │ (needs manual    │
                    └────────────┘         │  intervention)   │
                                           └──────────────────┘
```

- **`active`**: tools are flowing through the session normally.
- **`completed`**: the session finished without errors.
- **`failed`**: a tool raised an exception; the engine begins compensation.
- **`awaiting_approval`**: an irreversible tool is waiting for a human
  decision. The session is suspended until resolved.
- **`compensating`**: the engine is walking the undo stack, invoking
  compensations in LIFO order.
- **`compensated`**: all compensations completed successfully. The session is
  functionally rolled back.
- **`halted`**: a compensation itself failed (after exhausting all retries).
  The session requires manual operator intervention.

---

## Next steps

- See these concepts in action in the [Quickstart](quickstart.md).
- Deep dives into each concept live in the [Explanation section](../explanation/)
 : effect-journal internals, compensation orchestration, and the approval
  workflow.
