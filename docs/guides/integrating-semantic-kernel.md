---
title: "Integrate UndoLog with Semantic Kernel"
description: "Wire UndoLog interception, compensations, and approval gates into a Semantic Kernel agent."
section: "guides"
---
# Integrate UndoLog with Semantic Kernel

## Prerequisites

- UndoLog proxy running (see [Installation](../getting-started/installation.md))
- Semantic Kernel Python >= 1.0.0
- Python 3.10+
- `undolog-sdk` installed (`pip install undolog-sdk`)

## What you'll build

A Semantic Kernel agent that uses three plugins: one for safe knowledge retrieval, one for compensable data mutations, and one for irreversible operations that require human approval. UndoLog wraps each plugin function without modifying the kernel.

## Steps

### 1. Decorate kernel functions

Semantic Kernel plugins are plain Python functions registered with a kernel. Apply `@undolog_tool` directly:

```python
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from undolog_sdk import (
    undolog_tool,
    ToolTier,
    CompensationDescriptor,
    UndoLogSession,
    AwaitingApprovalError,
    run_with_session,
)

@undolog_tool(tier=ToolTier.SAFE)
async def search_documents(query: str) -> list[dict]:
    """Vector search over internal docs."""
    return [{"title": "Deployment guide", "score": 0.95}]

@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=CompensationDescriptor.new("revert_document_update"))
async def update_document(doc_id: str, content: str) -> dict:
    """Update a document (compensable: can be reverted)."""
    return {"doc_id": doc_id, "version": 3, "status": "updated"}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def archive_project(project_id: str) -> dict:
    """Archive an entire project (irreversible: requires approval)."""
    return {"project_id": project_id, "status": "archived"}
```

UndoLog awaits each decorated tool, so these must be `async def`. A sync
function raises `TypeError` at its first call, because there is no coroutine
to await.

### 2. Register plugins with the kernel

```python
kernel = Kernel()
kernel.add_service(OpenAIChatCompletion(service_id="gpt-4", ai_model_id="gpt-4"))

# Register undolog-wrapped functions as native plugins
from semantic_kernel.functions import KernelFunctionFromPrompt
kernel.add_function(plugin_name="docs", function_name="search", func=search_documents)
kernel.add_function(plugin_name="docs", function_name="update", func=update_document)
kernel.add_function(plugin_name="docs", function_name="archive", func=archive_project)
```

### 3. Wrap the execution in an UndoLog session

Semantic Kernel's `ChatCompletionAgent` calls functions through the kernel.
Opening an `UndoLogSession` is not enough on its own: `run_with_session`
publishes it to the context the tools read from, so every function the kernel
calls is tracked.

```python
from semantic_kernel.agents import ChatCompletionAgent

async def run_agent():
    session = UndoLogSession(org_id="org_prod", session_id="doc-workflow-1")
    agent = ChatCompletionAgent(
        service_id="gpt-4",
        kernel=kernel,
        name="DocManager",
        instructions="You manage documents. Search, update, and archive as needed.",
    )

    history = []
    try:
        async with run_with_session(session):
            async for response in agent.invoke(history):
                print(f"{response.role}: {response.content}")
    except AwaitingApprovalError as e:
        print(f"Approval needed for {e.tool_name}")
        print(f"POST /approvals/{e.approval_id}/approve to continue")
```

A tool called outside `run_with_session` can still be given the session
explicitly, as `_session`:

```python
result = await kernel.invoke(
    function=archive_project,
    arguments={"project_id": "proj_42", "_session": session},
)
```

### 4. Handle the approval gate

When an irreversible tool triggers `AwaitingApprovalError`, surface the approval
identifier and pause. The human approves via the dashboard (`GET /events`
SSE stream) or via `POST /approvals/{id}/approve`.

Once the approval is resolved, run the kernel again against the same journal.
Step positions are part of every call's signature, so the retry has to start
where the first run started: the same `session_id` with a fresh counter. The
calls that already completed then replay instead of running twice, provided
the retry makes the same calls in the same order.

```python
from undolog_sdk import AwaitingApprovalError, run_with_session

async def run_with_approval():
    session = UndoLogSession(org_id="org_prod", session_id="doc-workflow-1")
    agent = ChatCompletionAgent(
        service_id="gpt-4",
        kernel=kernel,
        name="DocManager",
        instructions="You manage documents. Search, update, and archive as needed.",
    )
    history = []
    try:
        async with run_with_session(session):
            async for response in agent.invoke(history):
                print(f"{response.role}: {response.content}")
    except AwaitingApprovalError as e:
        print(f"Awaiting approval: {e.approval_id}")
        print("Approve via the dashboard or POST /approvals/" + e.approval_id + "/approve")

    # Same session id and a fresh step counter, once the approval has
    # resolved: the retry reproduces the steps the first run journaled.
    resumed = UndoLogSession(org_id="org_prod", session_id=session.session_id)
    async with run_with_session(resumed):
        async for response in agent.invoke(history):
            print(f"{response.role}: {response.content}")
```

## Alternative: wrap the kernel

`wrap_semantic_kernel` returns a kernel facade that opens one session per
invocation, instruments the plugin functions you register through it, and sets
the context variable those functions read their session from, so no session has
to be threaded through by hand:

```python
from undolog_sdk.integrations import wrap_semantic_kernel

kernel = wrap_semantic_kernel(kernel, org_id="org_prod")
result = await kernel.invoke(function=archive_project, arguments={"project_id": "proj_42"})
```

Register functions through the facade rather than the kernel. A function the
kernel already holds cannot be instrumented afterwards, because it keeps its own
reference to the callable it wraps, which is why the facade instruments on the
way in. Functions already decorated with `@undolog_tool` come back untouched:

```python
kernel = wrap_semantic_kernel(kernel, org_id="org_prod")
kernel.add_function("docs", "search", search_documents)
kernel.add_function("docs", "update", update_document)
```

Raw functions need a classification, because the facade assigns one at
registration and `COMPENSABLE` requires a compensation name:

```python
kernel = wrap_semantic_kernel(
    kernel,
    org_id="org_prod",
    tiers={
        "search_documents": ToolTier.SAFE,
        "update_document": ToolTier.COMPENSABLE,
    },
    compensations={"update_document": "document_revision"},
)
```

Register a whole plugin in one call with `add_functions`. Every function is
accepted before any is registered, so one rejected function leaves the kernel
exactly as it was:

```python
kernel.add_functions(
    "docs",
    {"search": search_documents, "update": update_document},
)
```

An Irreversible function that needs a human raises `AwaitingApprovalError` out
of `invoke` or `invoke_prompt`. The facade records the run before it propagates,
so the handler already has what it needs to resume:

```python
try:
    result = await kernel.invoke(function=archive_project, arguments={"project_id": "proj_42"})
except AwaitingApprovalError as exc:
    print(f"Approve via the dashboard or POST /approvals/{exc.approval_id}/approve")
    result = await kernel.invoke(
        function=archive_project,
        arguments={"project_id": "proj_42", "session_id": kernel.session_id},
    )
```

`session_id` and `undolog_step_index` are consumed by the facade and never reach
the kernel's arguments. Resuming with `session_id` alone restarts the step
counter, so a retried invocation's calls land on the steps they already
journaled: the engine replays the ones that completed and lets the approved call
through, instead of running everything again. Add `undolog_step_index`
(`kernel.step_index`) to continue past those steps and start new work in the
same journal instead.

### Agents

`invoke` and `invoke_prompt` open a session per call, but an agent invokes the
kernel on its own schedule, so neither entry point covers it. Run the agent
inside `kernel.session()` instead: it publishes the same session to the context
variable the registered functions read, and the facade's properties report the
run while the block is open.

```python
from semantic_kernel.agents import ChatCompletionAgent

agent = ChatCompletionAgent(
    service_id="gpt-4",
    kernel=kernel,
    name="DocManager",
    instructions="You manage documents. Search, update, and archive as needed.",
)

try:
    async with kernel.session():
        async for response in agent.invoke([]):
            print(f"{response.role}: {response.content}")
except AwaitingApprovalError as exc:
    print(f"Approve via the dashboard or POST /approvals/{exc.approval_id}/approve")
    # Same session id, counter starting again at zero: the calls that
    # completed replay, and the approved call runs once.
    async with kernel.session(session_id=kernel.session_id):
        async for response in agent.invoke([]):
            print(f"{response.role}: {response.content}")
```

`session()` takes the same `session_id` and `start_step` as `invoke`. Leaving
them out opens a fresh journal, which is right for a new run but means a retry
re-runs everything, because the engine keys each call on its session id and
step position. To continue past the steps already journaled and start new work
in the same journal, pass `start_step=kernel.step_index` as well.

The facade calls the kernel's `add_function` with `plugin_name`, `function_name`,
and `func` as keyword arguments and forwards anything else you pass. If your
Semantic Kernel version names those parameters differently, the mismatch raises
`TypeError` at registration, before the kernel holds the function.

## Verify it works

```bash
python sk_agent.py
```

Expected output when the agent triggers `archive_project`:
```
Assistant: I found the document and updated it. Now I need to archive the project.
AwaitingApprovalError: approval_id=apr_xyz789, tool_name=archive_project
```

After approval:
```
Project archived successfully.
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `_session` parameter stripped | Kernel doesn't pass unknown params | Pass `_session` in `kernel.invoke(arguments={..., "_session": session})` |
| SAFE tools not tracked | Tier is correct | SAFE tier intentionally bypasses the log |
| `Connection refused` on intercept | Proxy not running | `docker compose up -d proxy` |
| Function registered but not called by LLM | LLM chose a different function | Check prompt: include plugin function descriptions |

## Next steps

- [Annotate tools](annotating-tools.md) with custom tiers and compensations
- [Configure approval gates](configuring-approval-gates.md) for Semantic Kernel workflows
- [Deploy with Docker](deploying-with-docker.md) the full Semantic Kernel + UndoLog stack
