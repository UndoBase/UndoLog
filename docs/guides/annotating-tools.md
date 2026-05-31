---
title: "How to annotate tools with @undolog_tool"
description: "## Prerequisites"
section: "guides"
---
# How to annotate tools with `@undolog_tool`

## Prerequisites

- [Install the Python SDK](../../sdks/undolog-py/README.md)
- A running UndoLog proxy on `http://localhost:8080`
- A PostgreSQL instance with the UndoLog migrations applied

## What you'll build

You will annotate three async Python functions as UndoLog tools: a SAFE search,
a COMPENSABLE email send with an undo handler, and an IRREVERSIBLE database
deletion that requires human approval. You will then write an agent loop that
handles the approval gate.

## Steps

### 1. Import the decorator and tier types

```python
from undolog_sdk import undolog_tool, ToolTier
from undolog_sdk.tier import CompensationDescriptor
from undolog_sdk.decorators import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession
```

### 2. Annotate a SAFE tool

```python
@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    return f"simulated results for: {query}"
```

SAFE tools bypass the effect log entirely. The function executes immediately and
no record is written.

### 3. Annotate a COMPENSABLE tool with a compensation descriptor

Capture the arguments **before** execution so the compensation function knows
what to undo.

```python
async def undo_send_email(to: str, subject: str, original_message_id: str) -> dict:
    return {"status": "recalled", "message_id": original_message_id}

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        "undo_send_email",
        args={"to": "{to}", "subject": "{subject}", "original_message_id": "{message_id}"},
    ),
)
async def send_email(to: str, subject: str, body: str, message_id: str) -> dict:
    return {"status": "sent", "message_id": message_id}
```

The `CompensationDescriptor.new()` shorthand uses default version (`1.0.0`),
`max_retries=3`, and `retry_backoff_ms=1000`. Pass a full `CompensationDescriptor`
to override any of these:

```python
CompensationDescriptor(
    fn_name="undo_send_email",
    fn_version="2.0.0",
    args={"to": "{to}"},
    max_retries=5,
    retry_backoff_ms=2000,
)
```

### 4. Annotate an IRREVERSIBLE tool

```python
@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_database(db_name: str) -> dict:
    return {"status": "deleted", "db_name": db_name}
```

When the proxy intercepts this call, it creates a pending approval record and
returns HTTP 202. The decorator raises `AwaitingApprovalError` instead of
executing the function body.

### 5. Handle approval in an agent loop

```python
async with UndoLogSession(org_id="org-demo") as session:
    # SAFE: executes immediately
    results = await search_web(query="latest news", _session=session)

    # COMPENSABLE: logs effect, executes, commits
    await send_email(
        to="user@example.com",
        subject="Hello",
        body="World",
        message_id="msg-001",
        _session=session,
    )

    # IRREVERSIBLE: raises AwaitingApprovalError
    try:
        await delete_database(db_name="prod-db", _session=session)
    except AwaitingApprovalError as err:
        print(f"Approval required: {err.approval_id}")
        print(f"Tool: {err.tool_name}, Step: {err.step_index}")
```

The pending approval appears at `GET /approvals?state=pending`. Resolve it with:

```bash
curl -X POST "http://localhost:8080/approvals/${APPROVAL_ID}/approve" \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: org-demo" \
  -d '{"actor": "admin@example.com"}'
```

Approval triggers auto-execution: the proxy runs the tool and commits the
result inline. The agent then retries the same step and receives the cached
result instead of executing the function again.

## Verify it works

Save the complete example to `test_annotations.py`:

```python
import asyncio
from undolog_sdk import undolog_tool, ToolTier
from undolog_sdk.tier import CompensationDescriptor
from undolog_sdk.decorators import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession

@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    return f"results for: {query}"

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_send_email"),
)
async def send_email(to: str) -> dict:
    return {"status": "sent", "to": to}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_database(db_name: str) -> dict:
    return {"status": "deleted"}

async def main():
    async with UndoLogSession(org_id="org-test") as session:
        r1 = await search_web(query="hello", _session=session)
        print("SAFE:", r1)
        r2 = await send_email(to="a@b.com", _session=session)
        print("COMPENSABLE:", r2)
        try:
            await delete_database(db_name="test", _session=session)
        except AwaitingApprovalError as e:
            print(f"IRREVERSIBLE blocked: approval_id={e.approval_id}")

asyncio.run(main())
```

Run it:

```bash
python test_annotations.py
```

Expected output (with a running proxy):

```
SAFE: results for: hello
COMPENSABLE: {'status': 'sent', 'to': 'a@b.com'}
IRREVERSIBLE blocked: approval_id=<uuid>
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: Tool 'send_email' requires a session` | Missing `_session` kwarg in call | Pass `_session=session` to every decorated tool |
| `ValueError: Compensable tools require a compensation descriptor` | COMPENSABLE tier with no `compensation=` arg | Add `compensation=CompensationDescriptor.new(...)` |
| `UndoLogClient` raises `ConnectError` | Proxy not running | Start proxy on `localhost:8080` or set `UNDOLOG_PROXY_URL` |
| Tool call hangs forever | No response from proxy | Check proxy logs with `docker compose logs -f proxy` |
| Approval not found on retry | Session or org ID mismatch | Verify `X-Org-Id` header matches session `org_id` |

## Next steps

- [Integrate with LangGraph](integrating-langgraph.md)
- [Write robust compensation handlers](writing-compensations.md)
- [Configure approval gates for production](configuring-approval-gates.md)
