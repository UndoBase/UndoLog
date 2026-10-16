---
title: "Migrating from _session injection to run_with_session"
description: "Move a codebase from explicit _session threading to the run_with_session context variable, one call site at a time, with both styles working throughout."
section: "guides"
---
# Migrating from `_session` injection to `run_with_session`

## Prerequisites

- [Install the Python SDK](../../sdks/undolog-py/README.md)
- Existing code that passes `_session=session` to decorated tools
- A running UndoLog proxy on `http://localhost:8080` for COMPENSABLE and
  IRREVERSIBLE calls; the SAFE tool in the examples needs none

## What changes

`undolog_tool` resolves the session for every call in a fixed order: an
explicit `_session` keyword argument wins when it is passed, otherwise
the session comes from the context variable that `run_with_session`
sets, and a call with neither raises `RuntimeError`. The migration
replaces the keyword threading with `async with run_with_session(session):`
at the entry point of each flow. Code that keeps passing `_session`
continues to work while you migrate call site by call site, because the
explicit argument still wins when passed.

## Steps

### 1. Wrap the flow instead of threading the session

Before, the session travels from the entry point to every call:

```python
import asyncio

from undolog_sdk import ToolTier, UndoLogSession, undolog_tool
from undolog_sdk.tier import CompensationDescriptor


@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    return f"results for: {query}"


@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_send_email"),
)
async def send_email(to: str) -> dict:
    return {"status": "sent", "to": to}


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        print(await search_web(query="undolog", _session=session))
        print(await send_email(to="user@example.com", _session=session))


asyncio.run(main())
```

After, the block publishes the session and the calls drop the keyword:

```python
import asyncio

from undolog_sdk import (
    ToolTier,
    UndoLogSession,
    run_with_session,
    undolog_tool,
)
from undolog_sdk.tier import CompensationDescriptor


@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    return f"results for: {query}"


@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_send_email"),
)
async def send_email(to: str) -> dict:
    return {"status": "sent", "to": to}


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        async with run_with_session(session):
            print(await search_web(query="undolog"))
            print(await send_email(to="user@example.com"))


asyncio.run(main())
```

Both scripts print the same two lines; see Verify it works below.

### 2. Drop the session parameter from helpers

Before, helpers take the session only to hand it to the tools inside:

```python
from undolog_sdk import UndoLogSession


async def refund_customer(session: UndoLogSession, order_id: str) -> dict:
    return await refund_order(order_id=order_id, _session=session)
```

After, the helper has no session parameter; the context supplies it:

```python
async def refund_customer(order_id: str) -> dict:
    return await refund_order(order_id=order_id)
```

Move the `run_with_session` block up to the entry point that owns the
flow: a request handler, a CLI `main()`, or one agent run. Keep the
block around the flow itself, and create tasks for that flow inside it.

### 3. Keep the explicit argument where two sessions are live

A batch worker holding two sessions still selects one per call with the
keyword argument:

```python
async def main() -> None:
    session_a = UndoLogSession(org_id="org-demo")
    session_b = UndoLogSession(org_id="org-demo")
    async with run_with_session(session_a):
        # The explicit argument picks session_b for this call.
        await send_email(to="batch-a@example.com", _session=session_b)
        # This call journals in session_a from the context.
        await send_email(to="batch-b@example.com")
```

## Interaction rules

Both styles are supported in one process, under these rules:

| Call | Session used |
|---|---|
| `_session=other` passed | `other`; the context is ignored |
| `_session` omitted or `None` | the context session |
| Neither present | `RuntimeError: Tool '...' requires a session` |

Inside the block, `get_current_session()` returns the context session
and `require_current_session()` raises `RuntimeError` when it is
absent. Entering `run_with_session` never contacts the server; exiting
resets the context variable and closes the shared default HTTP client,
which the next block reopens lazily. A nested `run_with_session`
replaces the context session for its own block and restores the outer
session on exit.

```python
import asyncio

from undolog_sdk import (
    ToolTier,
    UndoLogSession,
    get_current_session,
    run_with_session,
    undolog_tool,
)


@undolog_tool(tier=ToolTier.SAFE)
async def lookup_order(order_id: str) -> str:
    return f"order: {order_id}"


async def main() -> None:
    session_a = UndoLogSession(org_id="org-demo")
    session_b = UndoLogSession(org_id="org-demo")
    async with run_with_session(session_a):
        # An explicit argument wins over the context session.
        print(await lookup_order(order_id="1", _session=session_b))
        assert get_current_session() is session_a
        # Omitting the argument falls back to the context session.
        print(await lookup_order(order_id="2"))
        # Passing None falls back the same way.
        print(await lookup_order(order_id="3", _session=None))
    try:
        await lookup_order(order_id="4")
    except RuntimeError as err:
        print(err)


asyncio.run(main())
```

## Pitfalls

### Threads and context propagation

`asyncio` tasks copy the context at creation: a task created inside the
block sees the session, and a task created outside does not, even when
it is awaited inside the block. Threads start with an empty context:
pass `_session` explicitly there, or run the coroutine through
`contextvars.copy_context().run`.

```python
import asyncio
import threading

from undolog_sdk import ToolTier, UndoLogSession, run_with_session, undolog_tool


@undolog_tool(tier=ToolTier.SAFE)
async def lookup_order(order_id: str) -> str:
    return f"order: {order_id}"


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        async with run_with_session(session):
            # A task created inside the block inherits the session.
            print(await asyncio.create_task(lookup_order(order_id="in-task")))

    # A thread starts with an empty context, so the explicit argument
    # is the only way to reach a session from there.
    errors: list[RuntimeError] = []

    def call_without_session() -> None:
        try:
            asyncio.run(lookup_order(order_id="in-thread"))
        except RuntimeError as err:
            errors.append(err)

    worker = threading.Thread(target=call_without_session)
    worker.start()
    worker.join()
    print(errors[0])


asyncio.run(main())
```

### Forgetting the context manager

`run_with_session(session)` only builds the context manager; the
variable is set by `async with`. Without the block, or with a call
placed outside it, every tool raises the `requires a session` error
shown above.

### Mixing styles in one codebase

Mixing is safe: the explicit argument wins, so a call site missed
during the migration cannot land in the wrong session. Remove
`_session=session` one call site at a time and run the test suite
between batches.

## Verify it works

Save the before and after scripts from step 1 and run each against a
running proxy:

```bash
python before.py
python after.py
```

Both print:

```
results for: undolog
{'status': 'sent', 'to': 'user@example.com'}
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: Tool 'x' requires a session` | Call outside a block without `_session` | Wrap the flow in `run_with_session` or pass `_session` |
| Tool works in tests but fails in the worker | Thread started with an empty context | Pass `_session` or use `contextvars.copy_context().run` |
| Task raises `requires a session` inside the block | Task created before the block | Create the task inside `run_with_session` |
| Calls land in the wrong session | Stale explicit `_session` from the old style | Remove the keyword or pass the intended session |

## Next steps

- [Annotate tools with `@undolog_tool`](annotating-tools.md)
- [Integrate with LangGraph](integrating-langgraph.md)
- [Python SDK README](../../sdks/undolog-py/README.md)
