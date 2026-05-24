---
title: "Comparison with LangGraph"
description: "## What this document is"
section: "explanation"
---
# Comparison with LangGraph

## What this document is

A side-by-side comparison of UndoLog and LangGraph's safety and recovery
mechanisms. This is not a marketing comparison. It describes factual design
differences and the situations where each approach is more appropriate.

## Scope

LangGraph is an agent orchestration framework from LangChain. UndoLog is a
safety middleware layer for AI agent tool calls. They operate at different
layers of the stack and can be used together. UndoLog decorates tool calls
made within a LangGraph graph.

This comparison focuses only on the areas where their features overlap:
tool call safety, exactly-once execution, rollback, and human-in-the-loop
approval.

## Tool call safety

| Aspect | LangGraph | UndoLog |
|---|---|---|
| Safety model | No built-in tiers. All tool calls are equal. Developers add custom middleware. | Three tiers: SAFE, COMPENSABLE, IRREVERSIBLE. Built into the decorator. |
| Classification | Manual via middleware or prompt engineering. | Declarative via `@undolog_tool(tier=...)`. |
| Enforcement | By convention: the graph node decides. | Enforced at the proxy layer, bypassing the decorator still routes through the engine. |
| Audit trail | No built-in effect log. Developers implement their own. | Every non-SAFE call is logged to PostgreSQL with call signature, args, tier, and state. |

**When LangGraph is better:** You already have a custom safety layer and do
not want to add a dependency. Your tool calls are all safe or all
irreversible: no intermediate tier needed.

**When UndoLog is better:** You need an audit trail, exactly-once
guarantees, or a three-tier classification with built-in enforcement.

## Exactly-once execution

| Aspect | LangGraph | UndoLog |
|---|---|---|
| Mechanism | Node-level idempotency via checkpointing. If a node fails and restarts, the reducer determines whether to re-run. | Call-level idempotency via BLAKE3 signature + PostgreSQL advisory locks + UNIQUE constraint. |
| Granularity | Graph node level. If a node makes 3 tool calls, all 3 replay on retry. | Individual tool call level. Each call has an independent signature; replayed calls return cached results. |
| Cross-language | N/A. LangGraph is Python-only. | Python, Go, and Rust all compute identical signatures. |

**When LangGraph is better:** You need node-level idempotency where an
entire node's work should either complete fully or retry fully.

**When UndoLog is better:** You need per-call idempotency, especially when
individual tool calls have side effects that should not repeat. You need
cross-language consistency.

## Rollback and compensation

| Aspect | LangGraph | UndoLog |
|---|---|---|
| Automatic rollback | No. If a downstream node fails, earlier nodes are not automatically undone. | Yes. The Saga orchestrator walks the LIFO compensation stack. |
| Compensation registration | Manual: developers write custom error handlers. | Automatic, the `@undolog_tool` decorator pushes compensations before execution. |
| Crash recovery | Graph resumes from last checkpoint. Already-executed tool calls may run again. | Undo stack survives process death. On recovery, compensations run for all entries in `pending` state. |
| Multi-step rollback | Not supported natively. | LIFO ordering ensures dependent compensations run in correct order. |

**When LangGraph is better:** Your graph has no side effects, or you handle
rollback manually in error handlers.

**When UndoLog is better:** You need automatic, crash-safe rollback of
multi-step workflows with dependency ordering.

## Human-in-the-loop approval

| Aspect | LangGraph | UndoLog |
|---|---|---|
| Mechanism | `interrupt_after` pauses graph execution at a node boundary. Human provides input to continue. | Automatic per IRREVERSIBLE tool call. Session suspends until approval or rejection via REST or dashboard. |
| Granularity | Per-node. | Per-tool-call. Multiple irreversible calls in the same session each get their own approval. |
| Timeout | No built-in timeout. | 24-hour default timeout per approval request, configurable per org. Auto-approve or time-out option. |
| Audit trail | None built-in. | Full audit trail in `undolog_approval_events` table. |

**When LangGraph is better:** You need human input mid-graph (e.g., "review
this output and provide feedback"), not just approval for destructive
actions.

**When UndoLog is better:** You need per-call approval for destructive
actions with automatic timeout, audit trail, and REST API integration.

## Combined usage

UndoLog and LangGraph work together. UndoLog decorates the tool functions
that LangGraph nodes call. The session management is orthogonal. LangGraph
manages graph state, UndoLog manages tool call safety.

```python
from langgraph.graph import StateGraph
from undolog_sdk import undolog_tool, ToolTier, UndoLogSession

@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=...)
def create_user(payload: dict) -> dict:
    return db.insert("users", payload)

def create_user_node(state):
    async with UndoLogSession(org_id=state["org_id"]) as session:
        result = create_user(payload=state["payload"], _session=session)
        return {"user_id": result["id"]}

graph = StateGraph(MyState)
graph.add_node("create_user", create_user_node)
```

## Further reading

- [ADR 0006: MCP Protocol Layer](../adr/0006-mcp-protocol-layer.md), why
  UndoLog chose MCP-native over framework-specific integration
- [LangGraph documentation](https://langchain-ai.github.io/langgraph/) .
  for understanding LangGraph's native safety features
