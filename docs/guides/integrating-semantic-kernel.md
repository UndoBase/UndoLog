---
title: "Integrate UndoLog with Semantic Kernel"
description: "## Prerequisites"
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
from undolog_sdk import undolog_tool, ToolTier, CompensationDescriptor, UndoLogSession, AwaitingApprovalError

@undolog_tool(tier=ToolTier.SAFE)
def search_documents(query: str) -> list[dict]:
    """Vector search over internal docs."""
    return [{"title": "Deployment guide", "score": 0.95}]

@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=CompensationDescriptor.new("revert_document_update"))
def update_document(doc_id: str, content: str) -> dict:
    """Update a document (compensable: can be reverted)."""
    return {"doc_id": doc_id, "version": 3, "status": "updated"}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
def archive_project(project_id: str) -> dict:
    """Archive an entire project (irreversible: requires approval)."""
    return {"project_id": project_id, "status": "archived"}
```

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

Semantic Kernel's `ChatCompletionAgent` calls functions through the kernel. Use the session parameter to track calls:

```python
import asyncio
from semantic_kernel.agents import ChatCompletionAgent

async def run_agent():
    async with UndoLogSession(org_id="org_prod", session_id="doc-workflow-1") as session:
        agent = ChatCompletionAgent(
            service_id="gpt-4",
            kernel=kernel,
            name="DocManager",
            instructions="You manage documents. Search, update, and archive as needed.",
        )

        history = []
        try:
            async for response in agent.invoke(history):
                print(f"{response.role}: {response.content}")
        except AwaitingApprovalError as e:
            print(f"Approval needed for {e.tool_name}")
            print(f"POST /approvals/{e.approval_id}/approve to continue")
```

Semantic Kernel automatically discovers the `_session` parameter if present in the function signature. If your function does not receive a session, pass it explicitly:

```python
result = await kernel.invoke(
    function=archive_project,
    arguments={"project_id": "proj_42", "_session": session},
)
```

### 4. Handle the approval gate

When an irreversible tool triggers `AwaitingApprovalError`, surface the approval
identifier and pause. The human approves via the dashboard (`GET /events`
SSE stream) or via `POST /approvals/{id}/approve`. Once the approval is
resolved, retry the same tool call; the engine replays the cached result
instead of re-executing.

```python
from undolog_sdk import AwaitingApprovalError

async def run_with_approval():
    async with UndoLogSession(org_id="org_prod", session_id="doc-workflow-1") as session:
        agent = ChatCompletionAgent(
            service_id="gpt-4",
            kernel=kernel,
            name="DocManager",
            instructions="You manage documents. Search, update, and archive as needed.",
        )
        history = []
        try:
            async for response in agent.invoke(history):
                print(f"{response.role}: {response.content}")
        except AwaitingApprovalError as e:
            print(f"Awaiting approval: {e.approval_id}")
            print("Approve via the dashboard or POST /approvals/" + e.approval_id + "/approve")
            # After human approval, retry the same tool call.
            # The engine replays the cached result (no re-execution).
            async for response in agent.invoke(history):
                print(f"{response.role}: {response.content}")
```

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
