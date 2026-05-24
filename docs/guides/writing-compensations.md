---
title: "How to write effective compensations"
description: "## Prerequisites"
section: "guides"
---
# How to write effective compensations

## Prerequisites

- [Annotated tools](annotating-tools.md) with COMPENSABLE tier
- UndoLog engine running with the saga orchestrator enabled
- A compensation endpoint that your tool's undo function exposes

## What you'll build

You will write a compensation for a `delete_user` tool that is idempotent, safe
to retry, and captures the minimum data needed to undo the operation. You will
test it by simulating a mid-execution failure and verifying the compensation is
called.

## Steps

### 1. Capture the right arguments

A compensation needs the original identity of what was created. Never capture
derived data: capture the stable identifier.

```python
# ❌ Bad: username is mutable
CompensationDescriptor.new("restore_user", args={"username": "jdoe"})

# ✅ Good: user_id is the stable primary key
CompensationDescriptor.new("restore_user", args={"user_id": "usr_abc123"})

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("restore_user"),
)
async def delete_user(user_id: str, reason: str) -> dict:
    return {"status": "deleted", "user_id": user_id}
```

The `args` field is captured at decoration time. If you need dynamic values,
set them explicitly before the call:

```python
cd = CompensationDescriptor.new("restore_user", args={"user_id": user_id})
@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=cd)
```

### 2. Make compensations idempotent

The engine may call your compensation more than once. Use the
`Idempotency-Key` header (value: `undo-{undo_id}`) to guard against duplicate
processing.

```python
async def restore_user_handler(request):
    idempotency_key = request.headers.get("Idempotency-Key", "")
    if not idempotency_key.startswith("undo-"):
        return 400, "invalid key"
    if await already_processed(idempotency_key):
        return 200, {"status": "already_restored"}
    await restore_user(request.json["user_id"])
    await mark_processed(idempotency_key)
    return 200, {"status": "restored"}
```

### 3. Understand the retry strategy

`CompensationDescriptor` defaults:

| Field | Default | When to change |
|---|---|---|
| `max_retries` | `3` | Increase for unreliable downstream services |
| `retry_backoff_ms` | `1000` | Decrease for fast local compensations, increase for rate-limited APIs |

The engine uses exponential backoff: delay = `retry_backoff_ms * (2 ^ attempt)`.
A compensation with defaults waits 1s, 2s, then 4s before failing.

### 4. Handle permanent failures

If all retries are exhausted, the engine marks the compensation as
`compensation_failed`. The session enters a failed state and stops processing
further steps. Decide whether the system should halt or continue:

```python
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        "undo_send_email",
        max_retries=5,
        retry_backoff_ms=2000,
    ),
)
async def send_email(to: str, subject: str) -> dict:
    # ...
    pass
```

For compensations that must never fail (e.g., refunds), set a high `max_retries`
and alert on `compensation_failed` events in the dashboard.

### 5. Test the compensation

Create a test that mocks the compensation endpoint and triggers a tool failure:

```python
import asyncio
from undolog_sdk import undolog_tool, ToolTier
from undolog_sdk.tier import CompensationDescriptor
from undolog_sdk.session import UndoLogSession

call_log = []

async def undo_create_user(user_id: str):
    call_log.append(("undo", user_id))
    return {"status": "undone"}

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_create_user", args={"user_id": "usr_001"}),
)
async def create_user(name: str) -> dict:
    raise RuntimeError("simulated failure")

async def main():
    async with UndoLogSession(org_id="org-test") as session:
        try:
            await create_user(name="Alice", _session=session)
        except RuntimeError:
            print("Tool failed as expected")
    print("Compensation called:", len(call_log) > 0)

asyncio.run(main())
```

Run the test and check the engine logs for the compensation invocation:

```bash
python test_compensation.py
```

## Verify it works

Run the engine with `UNDOLOG_LOG_LEVEL=debug` and send a COMPENSABLE tool call
that fails. Search the logs for:

```
compensation dispatched    fn_name=undo_create_user args={"user_id":"usr_001"}
```

If your compensation endpoint echoes the call, check it received:

```json
{
  "fn_name": "undo_create_user",
  "args": {"user_id": "usr_001"},
  "idempotency_key": "undo-<effect_id>"
}
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Compensation never fires | Tool returned success (no error) | Only failed effects trigger compensation; raise an exception to test |
| Compensation fires but does nothing | Args missing the stable identifier | Capture `user_id`, not `username` |
| Duplicate compensation calls | Idempotency key not checked | Validate `Idempotency-Key: undo-{undo_id}` header at your endpoint |
| `compensation_failed` in dashboard | All retries exhausted | Increase `max_retries` or fix the compensation endpoint |
| Compensation runs out of LIFO order | Saga orchestrator uses execution order | Verify step indices are monotonic across the session |

## Next steps

- [Configure approval gates for irreversible tools](configuring-approval-gates.md)
- [Deploy with Docker Compose](deploying-with-docker.md)
- [Monitor production sessions](running-in-production.md)
