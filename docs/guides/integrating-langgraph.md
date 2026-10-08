---
title: "How to integrate UndoLog with LangGraph"
description: "Run a LangGraph agent's tool calls through UndoLog: annotate the tools, thread one session through the graph, and turn pending approvals into state."
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

## Alternative: wrap the compiled graph

Threading `_session` through every node is optional. `wrap_langgraph`
opens one session for the whole run, as long as the tools the nodes call
are instrumented: decorate them with `@undolog_tool` as above, or build
the graph from a list returned by `wrap_tools`.

```python
from undolog_sdk.integrations import wrap_langgraph

app = wrap_langgraph(graph, org_id="org-demo")

result = await app.ainvoke({"query": "urgent payment needed"})
if result["awaiting_approval"]:
    approval_id = result["approval_request"]["approval_id"]
    print(f"Approve via the dashboard or POST /approvals/{approval_id}/approve")
    # Once the approval resolves, re-invoke with the returned state.
    result = await app.ainvoke(result)
```

`ainvoke` returns the graph state plus `session_id`,
`undolog_step_index`, `awaiting_approval`, and `approval_request`.
Feeding that state back in resumes the same session, and the engine
replays the steps that already completed, provided the retried run makes
the same calls in the same order. Replay is keyed on each call's
position, arguments, and tool, so a retry that branches differently is
journaled as new work rather than replayed. Three rules:

- Instrument the tools before compiling the graph: a compiled graph
  holds its own references to them, so `wrap_langgraph` cannot reach
  them afterwards.
- Put `session_id` and `undolog_step_index` in `AgentState` so
  LangGraph persists them with the rest of the state across checkpoint
  restores.
- Only `ainvoke` is instrumented. For `astream`, open your own
  `UndoLogSession` and `run_with_session` around the call.

To instrument a list of tools that are not decorated yet, pass the tier
overrides and the compensation registry names explicitly:

```python
from undolog_sdk.integrations import wrap_tools

tools = wrap_tools(
    [search_web, create_user, payout],
    tiers={"search_web": ToolTier.SAFE, "payout": ToolTier.IRREVERSIBLE},
    compensations={"create_user": "undo_create_user"},
)
```

`wrap_tools` raises `ValueError` when a COMPENSABLE tool has no
compensation name: the tool would then run with no journal, no
compensation, and no approval gate.

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
| `RuntimeError: Tool 'x' requires a session` | No session in state and no session in the context var | Set `state["session"]` before `graph.ainvoke`, or open `run_with_session` around the call |
| Step indices skip or duplicate | Multiple `UndoLogSession` instances used | Create one session per graph run, share via state |
| `LangGraphException: node not found` | Route returns wrong name | Check the string returned by `route_after_search` matches an `add_node` name |
| Compensations not firing on node failure | Exception raised before `next_step()` | Ensure node awaits the tool after session is active |
| `AwaitingApprovalError` stops the graph | IRREVERSIBLE tool without prior approval | Catch the error in the node, or wrap the graph with `wrap_langgraph`, which returns it as `awaiting_approval` state |

## Next steps

- [Write robust compensation handlers](writing-compensations.md)
- [Configure approval gates](configuring-approval-gates.md)
- [Deploy the full stack with Docker](deploying-with-docker.md)
