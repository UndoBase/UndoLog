---
title: "How to integrate UndoLog with LangGraph"
description: "## Prerequisites"
section: "guides"
---
# How to integrate UndoLog with LangGraph

## Prerequisites

- [Install the Python SDK](../../sdks/undolog-py/README.md)
- LangGraph >= 0.2.0 (`pip install langgraph`)
- A running UndoLog proxy at `http://localhost:8080`
- [Annotated tools](annotating-tools.md) for SAFE, COMPENSABLE, and IRREVERSIBLE operations

## What you'll build

You will create a LangGraph `StateGraph` where each node is an UndoLog-decorated
tool. The session flows through the graph state so that step indices are
monotonically ordered across the entire run. A failure in a COMPENSABLE node
triggers LIFO compensation via the engine.

## Steps

### 1. Define the graph state

```python
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from undolog_sdk.session import UndoLogSession
import operator

class AgentState(TypedDict):
    session: UndoLogSession
    query: str
    search_results: str
    user_email: str
    errors: Annotated[list[str], operator.add]
```

### 2. Define the tool nodes

Each node receives the session via the graph state and passes it as `_session`.

```python
from undolog_sdk import undolog_tool, ToolTier
from undolog_sdk.tier import CompensationDescriptor
from undolog_sdk.decorators import AwaitingApprovalError

@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    return f"results for {query}"

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_create_user"),
)
async def create_user(email: str) -> dict:
    return {"status": "created", "email": email}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def payout(amount: float, user_id: str) -> dict:
    return {"status": "paid", "amount": amount, "user_id": user_id}
```

### 3. Wrap the tools as LangGraph nodes

```python
async def search_node(state: AgentState) -> dict:
    result = await search_web(query=state["query"], _session=state["session"])
    return {"search_results": result}

async def create_user_node(state: AgentState) -> dict:
    result = await create_user(email=state["user_email"], _session=state["session"])
    return {"search_results": f"user {result}"}
```

For branching. SAFE goes to COMPENSABLE or IRREVERSIBLE:

```python
def route_after_search(state: AgentState) -> str:
    if "urgent" in state["query"].lower():
        return "payout_node"
    return "create_user_node"

async def payout_node(state: AgentState) -> dict:
    try:
        result = await payout(amount=100.0, user_id="usr-001", _session=state["session"])
        return {"search_results": f"payout {result}"}
    except AwaitingApprovalError as e:
        return {"errors": [f"approval needed: {e.approval_id}"]}
```

### 4. Build the graph

```python
builder = StateGraph(AgentState)

builder.add_node("search", search_node)
builder.add_node("create_user", create_user_node)
builder.add_node("payout", payout_node)

builder.add_edge(START, "search")
builder.add_conditional_edges("search", route_after_search)
builder.add_edge("create_user", END)
builder.add_edge("payout", END)

graph = builder.compile()
```

### 5. Run the graph inside a session

```python
import asyncio

async def main():
    async with UndoLogSession(org_id="org-demo") as session:
        state = AgentState(
            session=session,
            query="urgent payment needed",
            search_results="",
            user_email="new@user.com",
            errors=[],
        )
        result = await graph.ainvoke(state)
        print(result["search_results"])
        print(result["errors"])

asyncio.run(main())
```

The session ensures that `step_index` increments across all nodes in graph
traversal order: search (1) → payout (2). If payout fails and compensation is
triggered, the engine undoes in LIFO order: only payout is rolled back because
search was SAFE (no effect logged).

## Verify it works

Save the complete script as `test_langgraph.py` and run it:

```bash
python test_langgraph.py
```

Expected output when the proxy is running:

```
payout {'status': 'paid', 'amount': 100.0, 'user_id': 'usr-001'}
[]
```

With `query="normal"` (routes to create_user):

```
user {'status': 'created', 'email': 'new@user.com'}
[]
```

Check the effect log in the dashboard at `http://localhost:3000`. You should see
one effect entry per non-SAFE node with `status: committed`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: session_id required` | Session not passed in state | Set `state["session"]` before calling `graph.ainvoke` |
| Step indices skip or duplicate | Multiple `UndoLogSession` instances used | Create one session per graph run, share via state |
| `LangGraphException: node not found` | Route returns wrong name | Check the string returned by `route_after_search` matches an `add_node` name |
| Compensations not firing on node failure | Exception raised before `next_step()` | Ensure node awaits the tool after session is active |
| `AwaitingApprovalError` stops the graph | IRREVERSIBLE tool without prior approval | Catch the error in the node, surface the `approval_id`, or route to a human |

## Next steps

- [Write robust compensation handlers](writing-compensations.md)
- [Configure approval gates](configuring-approval-gates.md)
- [Deploy the full stack with Docker](deploying-with-docker.md)
