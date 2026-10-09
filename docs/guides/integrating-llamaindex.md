---
title: "Integrate UndoLog with LlamaIndex"
description: "Wire UndoLog interception, compensations, and approval gates into a LlamaIndex agent."
section: "guides"
---
# Integrate UndoLog with LlamaIndex

## Prerequisites

- UndoLog proxy running (see [Installation](../getting-started/installation.md))
- LlamaIndex >= 0.12
- Python 3.10+
- `undolog-sdk` installed (`pip install undolog-sdk`)

## What you'll build

A LlamaIndex agent backed by three tools: a safe knowledge lookup, a compensable ticket update, and an irreversible message that waits for a human. Every tool call runs inside an UndoLog session, so each one is journaled, replays after a retry instead of running twice, and pauses at the approval gate when it is irreversible.

## Steps

### 1. Define your tools with UndoLog

```python
from undolog_sdk import (
    undolog_tool,
    ToolTier,
    CompensationDescriptor,
)

@undolog_tool(tier=ToolTier.SAFE)
async def search_articles(query: str) -> list[dict]:
    """Search the knowledge base (safe: read-only)."""
    return [{"title": "UndoLog in production", "url": "..."}]

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("ticket_revision"),
)
async def update_ticket(ticket_id: str, note: str) -> dict:
    """Append a note to a ticket (compensable: can be reverted)."""
    return {"ticket_id": ticket_id, "status": "updated"}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def send_message(recipient: str, body: str) -> dict:
    """Message the customer (irreversible: requires approval)."""
    return {"recipient": recipient, "sent": True}
```

UndoLog awaits each decorated tool, so these must be `async def`. A sync function raises `TypeError` at its first call, because there is no coroutine to await.

### 2. Hand the tools to LlamaIndex

LlamaIndex calls a tool through `FunctionTool`. Build one per function and pass them to the agent:

```python
from llama_index.core.agent import AgentRunner
from llama_index.core.tools import FunctionTool

tools = [
    FunctionTool.from_defaults(
        fn=search_articles,
        name="search_articles",
        description="Search the knowledge base",
    ),
    FunctionTool.from_defaults(
        fn=update_ticket,
        name="update_ticket",
        description="Append a note to a support ticket",
    ),
    FunctionTool.from_defaults(
        fn=send_message,
        name="send_message",
        description="Send a message to the customer",
    ),
]

agent = AgentRunner.from_llm(llm=llm, tools=tools)
```

UndoLog's decorator preserves the function's name and signature with `functools.wraps`, so `FunctionTool` sees the parameters the original function declared.

### 3. Run the query inside a session

Opening an `UndoLogSession` is not enough on its own: `run_with_session` publishes it to the context the tools read from, so every call the agent makes is tracked. The agent then runs with `aquery`, since the decorated tools are coroutines:

```python
from undolog_sdk import UndoLogSession, run_with_session

async def query_with_undolog(prompt: str) -> None:
    session = UndoLogSession(org_id="org_prod", session_id="support-42")
    async with run_with_session(session):
        response = await agent.aquery(prompt)
        print(response)
```

### 4. Handle the approval flow

When an irreversible tool triggers `AwaitingApprovalError`, surface the approval identifier and pause. The human approves through the dashboard (`GET /events` SSE stream) or `POST /approvals/{id}/approve`.

Once the approval is resolved, query again against the same journal. Step positions are part of every call's signature, so the retry has to start where the first run started: the same `session_id` with a fresh counter. The calls that already completed then replay instead of running twice, provided the retried run makes the same calls in the same order.

```python
from undolog_sdk import AwaitingApprovalError, UndoLogSession, run_with_session

async def query_with_approval(prompt: str) -> None:
    session = UndoLogSession(org_id="org_prod", session_id="support-42")
    try:
        async with run_with_session(session):
            response = await agent.aquery(prompt)
    except AwaitingApprovalError as e:
        print(f"Awaiting approval: {e.approval_id}")
        print("Approve via the dashboard or POST /approvals/" + e.approval_id + "/approve")

        # Same session id and a fresh step counter, once the approval has
        # resolved: the retry reproduces the steps the first run journaled.
        resumed = UndoLogSession(org_id="org_prod", session_id=session.session_id)
        async with run_with_session(resumed):
            response = await agent.aquery(prompt)

    print(response)
```

## Alternative: wrap the index

`wrap_llamaindex` opens one session for the query, instruments the index's tools, and sets the context variable the tools read their session from, so no session has to be threaded through the agent by hand:

```python
from undolog_sdk.integrations import wrap_llamaindex

index = wrap_llamaindex(agent, org_id="org_prod")
result = await index.aquery(prompt)
```

The facade wraps anything exposing `aquery` and `tools`, which is the duck-typed surface this integration assumes: `aquery` is the async query entry point LlamaIndex agents and query engines expose, `tools` is the list the wrapper reassigns with its instrumented copies, and a `FunctionTool` built from an `async def` exposes that function as its `coroutine`, the attribute the wrapper swaps. An object that holds its tools elsewhere is refused with `AttributeError` rather than queried with tools the wrapper never reached; decorate those tools with `@undolog_tool` and open `run_with_session` around the query yourself, as the steps above do.

Tools already decorated with `@undolog_tool` come back untouched. Raw tools need a classification, because `wrap_llamaindex` assigns one at query time:

```python
index = wrap_llamaindex(
    agent,
    org_id="org_prod",
    tiers={
        "search_articles": ToolTier.SAFE,
        "send_message": ToolTier.IRREVERSIBLE,
    },
    compensations={"update_ticket": "ticket_revision"},
)
```

An `IRREVERSIBLE` tool that needs a human raises `AwaitingApprovalError` out of `aquery`. The facade records the run before it propagates, so the handler already has what it needs to resume:

```python
try:
    result = await index.aquery(prompt)
except AwaitingApprovalError as exc:
    print(f"Approve via the dashboard or POST /approvals/{exc.approval_id}/approve")
    result = await index.aquery(prompt, session_id=index.session_id)
```

`session_id` and `undolog_step_index` are consumed by the wrapper as query keywords and never reach the index, so UndoLog bookkeeping is not mistaken for a query option. Resuming with `session_id` alone restarts the step counter, so a retried run's calls land on the steps they already journaled: the engine replays the ones that completed and lets the approved call through, instead of running everything again. Add `undolog_step_index` (`index.step_index`) to continue past those steps and start new work in the same journal instead.

That replay holds only if the retried run makes the same tool calls in the same order: step positions are part of every call's signature, so a different sequence is a different set of operations.

Four rules:

- Only `aquery` opens a session and wraps the tools. The other async entry points (`achat`, `astream`, `arun`) get neither, so a decorated tool raises `RuntimeError` for the missing session. Open `run_with_session` around one of them yourself.
- Tools must be async. `undolog_tool` awaits the function it wraps, so `wrap_llamaindex` rejects a sync tool with `ValueError` rather than wrapping one that fails at its first call.
- A raw tool that is not listed in `tiers` defaults to `COMPENSABLE`, and `wrap_llamaindex` raises `ValueError` when it has no compensation name: it would then run with no journal, no compensation, and no approval gate.
- The wrap is idempotent, so a second query neither re-wraps nor journals a call twice, and every tool is accepted before the list is reassigned, so one rejected tool leaves the index exactly as it was.

## Verify it works

Save the steps above as `llamaindex_with_undolog.py` and run it with the proxy up:

```bash
python llamaindex_with_undolog.py
```

The approval handler prints what a human has to resolve:

```
Awaiting approval: apr_abc123
Approve via the dashboard or POST /approvals/apr_abc123/approve
```

Approve it with `POST /approvals/apr_abc123/approve`, then run the query again. Step 3 fixed the session id to `support-42`, so the second run resumes that journal: calls that already completed replay instead of running twice, and the proxy's `GET /events` lists each tool call once.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `UndoLogClientError: Connection refused` | Proxy not running | Start the proxy: `docker compose up -d proxy` |
| Tools execute but no session tracked | Session not passed to the agent | Wrap the query in `async with run_with_session(...)` |
| `RuntimeError` for a missing session | Query ran through `achat` or `astream` | Open `run_with_session` around that entry point |
| `AwaitingApprovalError` never raised | Tool tier not set to IRREVERSIBLE | Check `@undolog_tool(tier=ToolTier.IRREVERSIBLE)` |
| `ValueError: wrap_llamaindex cannot instrument ...` | Tool is a sync function | Declare the tool `async def` |
| `ValueError: Compensable tool ... has no compensation name` | Raw tool with no tier or compensation | List it in `tiers=` or `compensations=` |
| `AttributeError: wrap_llamaindex needs the index to expose 'tools'` | The agent keeps its tools elsewhere | Decorate them with `@undolog_tool` and open `run_with_session` |
| `aquery` returned while a tool awaits approval | The agent caught `AwaitingApprovalError` | Find the `approval_required` log line; the approval is already in the engine |

## Next steps

- [Configure approval gates](configuring-approval-gates.md) for production approval workflows
- [Write compensations](writing-compensations.md) that handle partial failures
- [Run in production](running-in-production.md) with proper scaling
