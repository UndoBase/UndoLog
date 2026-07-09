"""Stateful LangGraph agent with UndoLog approval branching.

Extends ``agent.py`` with three LangGraph patterns for stateful
approval branching:

1. **Custom StateGraph** : defines ``AgentState`` with explicit fields
   for session identity, halt flag, and pending approval metadata.

2. **Human-in-the-loop via ``interrupt``**. When an IRREVERSIBLE tool
   raises ``AwaitingApprovalError`` the graph pauses and surfaces the
   approval request to the caller.  The caller can ``Command(resume=...)``
   with ``"approve"`` or ``"reject"`` to continue.

3. **Conditional branching** : after tool execution the graph routes to
   ``END`` on rejection or continues the model-tool loop on approval,
   demonstrating undolog's approve→continue / reject→halt pattern.

Graph structure::

    model ──(tool_calls)──► tools ──(halted)──► END
       ▲                       │
       └──(continue)───────────┘

    Inside ``tools`` node:
        try tool()
        except AwaitingApprovalError ──► interrupt({approval_id, ...})
                                              │
                                    ┌─────────┴─────────┐
                                    │ approve            │ reject
                                    ▼                    ▼
                              approve_via_api()      halt=True
                              re-execute tool()      return
                              return result           END

Usage
-----
::

    python examples/langchain-support-agent/agent_stateful.py

To exercise the approval branch::

    python examples/langchain-support-agent/agent_stateful.py --escalate

Environment
-----------
Same as ``agent.py`` plus:
INTERRUPT_MODE : str
    ``"cli"`` (default) : prints interrupt and waits for stdin.
    ``"auto"`` : auto-approves every pending approval (for testing).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Optional, TypedDict

import httpx
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from undolog_sdk import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession

_examples_root = os.path.join(os.path.dirname(__file__), "..")
if _examples_root not in sys.path:
    sys.path.insert(0, _examples_root)

from example_tools import get_tool_registry  # noqa: E402  -- sys.path insertion above

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("agent_stateful")


# ── Graph state ──────────────────────────────────────────────────────────────


class AgentState(TypedDict):
    """Persistent state for the LangGraph agent.

    Fields
    ------
    messages : list[BaseMessage]
        Chat history visible to the LLM (Human → AI → Tool → AI …).
    org_id : str
        Organisation identifier for the UndoLog session.
    session_id : str
        Stable session identifier (survives checkpoint restores).
    halted : bool
        ``True`` when a tool was rejected; the graph routes to ``END``.
    pending_approval : dict | None
        Captured ``AwaitingApprovalError`` metadata set before ``interrupt``.
        Shape: ``{"approval_id": str, "tool_name": str, "tool_call": dict}``.
    """

    messages: list[BaseMessage]
    org_id: str
    session_id: str
    halted: bool
    pending_approval: Optional[dict]


def _proxy_url() -> str:
    return os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


def _api_key() -> str | None:
    return os.environ.get("UNDOLOG_API_KEY") or None


def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = _api_key()
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


# ── Graph nodes ──────────────────────────────────────────────────────────────


async def call_model(state: AgentState) -> dict[str, Any]:
    """Call the LLM to generate the next action.

    Appends the AI response (which may contain ``tool_calls``) to the
    message list.  The subsequent ``tools`` node will execute them
    through the UndoLog interceptor.
    """
    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        msg = "Set OPENAI_API_KEY environment variable"
        return {"messages": [HumanMessage(content=msg)]}

    tool_registry = get_tool_registry()
    llm = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url)
    bound = llm.bind_tools(list(tool_registry.values()))

    response = await bound.ainvoke(state["messages"])
    return {"messages": [response]}


async def execute_tools(state: AgentState) -> dict[str, Any]:
    """Execute pending tool calls through the UndoLog interceptor.

    Catches ``AwaitingApprovalError`` from IRREVERSIBLE tools and
    pauses the graph via ``interrupt()``.  The external caller sends
    ``Command(resume="approve")`` or ``Command(resume="reject")`` to
    continue.

    On approval the graph calls the proxy's approve endpoint *and*
    re-executes the tool.  On rejection the graph sets ``halted=True``
    which routes to ``END``.
    """
    last_message = state["messages"][-1]
    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {}

    pending = state.get("pending_approval")
    session = _build_session(state)

    if pending:
        resp = interrupt()
        if resp == "approve":
            log.info("Approval granted. Resolving approval_id=%s", pending["approval_id"])
            await _approve_via_api(pending["approval_id"])
            result = await _invoke_tool(pending["tool_call"], session)
            return {
                "messages": [result],
                "pending_approval": None,
            }
        log.info("Approval rejected. Halting workflow")
        return {"halted": True, "pending_approval": None}

    tool_call = last_message.tool_calls[0]
    try:
        result = await _invoke_tool(tool_call, session)
        return {"messages": [result]}
    except AwaitingApprovalError as exc:
        log.info(
            "IRREVERSIBLE tool requires approval: approval_id=%s tool=%s",
            exc.approval_id,
            exc.tool_name,
        )
        interrupt(
            {
                "type": "approval_required",
                "approval_id": exc.approval_id,
                "tool_name": exc.tool_name,
                "tool_call": tool_call,
            }
        )
        return {
            "pending_approval": {
                "approval_id": exc.approval_id,
                "tool_name": exc.tool_name,
                "tool_call": tool_call,
            },
        }


def route_after_tools(state: AgentState) -> str:
    """Conditional edge after the ``tools`` node.

    Returns
    -------
    str
        ``"end"`` when the workflow is halted or no tool calls remain.
        ``"continue"`` when the model should be called again.
    """
    if state.get("halted"):
        return "end"
    if state.get("pending_approval"):
        return "end"
    messages = state.get("messages", [])
    if not messages:
        return "end"
    last = messages[-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "continue"
    return "end"


# ── Graph construction ──────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    """Build and compile the LangGraph with checkpointer and interrupt support.

    Returns
    -------
    StateGraph
        Compiled graph ready for ``ainvoke`` with ``Command`` support.
    """
    builder = StateGraph(AgentState)

    builder.add_node("model", call_model)
    builder.add_node("tools", execute_tools)

    builder.set_entry_point("model")
    builder.add_edge("model", "tools")
    builder.add_conditional_edges(
        "tools",
        route_after_tools,
        {"continue": "model", "end": END},
    )

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_session(state: AgentState) -> UndoLogSession:
    """Reconstruct an ``UndoLogSession`` from graph state.

    Uses the stable ``session_id`` from state so that checkpoint
    restores produce the same session identity.
    """
    org_id = state.get("org_id", os.environ.get("UNDOLOG_ORG_ID", "org_demo"))
    session_id = state.get("session_id")
    session = UndoLogSession(org_id=org_id)
    if session_id:
        object.__setattr__(session, "session_id", session_id)
    object.__setattr__(session, "_step_index", 0)
    return session


async def _invoke_tool(
    tool_call: dict[str, Any],
    session: UndoLogSession,
) -> ToolMessage:
    """Invoke one tool through the UndoLog interceptor.

    Parameters
    ----------
    tool_call : dict
        LangGraph tool call with ``name``, ``args``, ``id``.
    session : UndoLogSession
        Active undolog session threaded via ``_session=``.

    Returns
    -------
    ToolMessage
        Result message with tool output or error.
    """
    registry = get_tool_registry()
    tool_name = tool_call["name"]
    tool_fn = registry.get(tool_name)
    if tool_fn is None:
        return ToolMessage(
            content=f"Unknown tool: {tool_name}",
            tool_call_id=tool_call["id"],
        )

    try:
        result = await tool_fn(**tool_call["args"], _session=session)
        return ToolMessage(
            content=json.dumps(result, indent=2),
            tool_call_id=tool_call["id"],
        )
    except AwaitingApprovalError:
        raise
    except Exception as exc:
        return ToolMessage(
            content=f"Error executing {tool_name}: {exc}",
            tool_call_id=tool_call["id"],
        )


async def _approve_via_api(approval_id: str) -> dict[str, Any]:
    """Approve a pending approval via the proxy REST API.

    ``POST /approvals/{approval_id}/approve``
    """
    url = f"{_proxy_url()}/approvals/{approval_id}/approve"
    body = {"actor": "agent_stateful", "note": "Approved via LangGraph interrupt resume"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


# ── CLI runner ───────────────────────────────────────────────────────────────


async def _resolve_interrupt(approval_id: str) -> str:
    """Resolve an approval interrupt via CLI prompt or env config.

    In ``auto`` mode (``INTERRUPT_MODE=auto``) returns ``"approve"``
    immediately without prompting.
    """
    mode = os.environ.get("INTERRUPT_MODE", "cli")
    if mode == "auto":
        log.info("Auto-approving approval_id=%s", approval_id)
        return "approve"
    print(f"\nApproval required: approval_id={approval_id}")
    print("Enter 'approve' to continue, 'reject' to halt:")
    resp = (await asyncio.to_thread(sys.stdin.readline)).strip().lower()
    return resp if resp in ("approve", "reject") else "reject"


async def main() -> None:
    """Run the stateful LangGraph agent with approval branching.

    Creates a session, builds the graph, and invokes it.  If the graph
    pauses for approval the CLI runner prompts for a decision.
    """
    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    session = UndoLogSession(org_id=org_id)

    graph = build_graph()

    config = {
        "configurable": {"thread_id": session.session_id},
    }

    use_escalate = "--escalate" in sys.argv

    prompt = (
        "A premium customer named Alice (cust_42) reports she cannot "
        "access the enterprise dashboard. Look up her info, create a "
        "medium priority support ticket, and send an acknowledgment email."
    )
    if use_escalate:
        prompt += " Then escalate the case."

    log.info("Session: %s", session.session_id)
    log.info("Org:    %s", org_id)

    state: dict[str, Any] = {
        "messages": [HumanMessage(content=prompt)],
        "org_id": org_id,
        "session_id": session.session_id,
        "halted": False,
        "pending_approval": None,
    }

    while True:
        result = await graph.ainvoke(state, config)
        state = result

        if state.get("halted"):
            log.info("Workflow halted after rejection")
            break

        if state.get("pending_approval"):
            approval_id = state["pending_approval"]["approval_id"]
            decision = await _resolve_interrupt(approval_id)
            state = await graph.ainvoke(
                Command(resume=decision),
                config,
            )
            continue

        break

    last_msg = state["messages"][-1]
    content = getattr(last_msg, "content", str(last_msg))
    log.info("Final: %s", content[:200] if len(content) > 200 else content)


if __name__ == "__main__":
    asyncio.run(main())
