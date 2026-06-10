"""
Run the LangChain agent against a live UndoLog stack.

This runner creates ``StructuredTool`` wrappers with explicit JSON schemas
so the LLM receives well-typed tool definitions.  A context variable
threads the ``UndoLogSession`` through every tool invocation.

The agent is provider-agnostic: it works with any OpenAI-compatible LLM
provider (OpenAI, Groq, Together, etc.).  Set ``OPENAI_BASE_URL`` and
``OPENAI_MODEL`` to switch providers.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections.abc import Callable, Coroutine
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from undolog_sdk.session import UndoLogSession

from tools import get_tool_registry

_current_session: contextvars.ContextVar[UndoLogSession | None] = contextvars.ContextVar(
    "undolog_session", default=None
)

TOOL_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "lookup_customer",
        "desc": "Look up customer information by customer_id.",
        "args": {"customer_id": {"type": "string", "description": "Customer ID"}},
    },
    {
        "name": "send_email",
        "desc": "Send an email to a customer. Args: to (email), subject, body.",
        "args": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Email body content"},
        },
    },
    {
        "name": "create_ticket",
        "desc": "Create a support ticket. Args: customer_id, priority, description.",
        "args": {
            "customer_id": {"type": "string", "description": "Customer ID"},
            "priority": {
                "type": "string",
                "description": "Priority: low, medium, high, critical",
            },
            "description": {"type": "string", "description": "Issue description"},
        },
    },
    {
        "name": "escalate_case",
        "desc": "Escalate a case. IRREVERSIBLE - requires approval.",
        "args": {
            "ticket_id": {"type": "string", "description": "Ticket ID"},
            "reason": {"type": "string", "description": "Escalation reason"},
        },
    },
]


def _make_tool_fn(
    name: str,
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Wrap a raw UndoLog tool so it receives the session from the context var.

    Parameters
    ----------
    name : str
        Tool name (used in error messages).
    raw_fn : callable
        The UndoLog-decorated async function.

    Returns
    -------
    callable
        Async function that injects ``_session`` before calling ``raw_fn``.
    """

    async def tool_fn(**kwargs: Any) -> Any:
        session = _current_session.get()
        if session is None:
            raise RuntimeError(f"Tool '{name}' called outside an active UndoLog session")
        return await raw_fn(**kwargs, _session=session)

    return tool_fn


def make_lc_tool(
    name: str,
    desc: str,
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> StructuredTool:
    """Build a LangChain ``StructuredTool`` from a raw UndoLog tool.

    Parameters
    ----------
    name : str
        Tool name exposed to the LLM.
    desc : str
        Natural-language description of the tool.
    raw_fn : callable
        The UndoLog-decorated async function.

    Returns
    -------
    StructuredTool
        A LangChain-compatible tool wrapper.
    """
    return StructuredTool.from_function(
        name=name,
        description=desc,
        func=None,
        coroutine=_make_tool_fn(name, raw_fn),
    )


async def main() -> None:
    """Run the agent against the live UndoLog stack.

    Expects
    -------
    UNDOLOG_ORG_ID : str
        Organisation identifier (default ``org_demo``).
    OPENAI_API_KEY : str
        API key for the LLM provider.
    OPENAI_BASE_URL : str
        Base URL for the OpenAI-compatible API (default ``https://api.openai.com/v1``).
    OPENAI_MODEL : str
        Model name (default ``gpt-4o``).

    Notes
    -----
    The agent uses four tools (lookup, email, ticket, escalate) and reports
    the final LLM response.  IRREVERSIBLE tools are blocked until a human
    approves via the proxy approval endpoint.
    """
    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")

    if not api_key:
        print("ERROR: Set OPENAI_API_KEY environment variable")
        return

    raw_tools = get_tool_registry()
    lc_tools: list[StructuredTool] = []
    for cfg in TOOL_CONFIGS:
        name: str = cfg["name"]
        raw_fn = raw_tools.get(name)
        if raw_fn is not None:
            lc_tools.append(make_lc_tool(name, cfg["desc"], raw_fn))

    print(f"LLM model: {model}")
    print(f"Org:       {org_id}")
    print(f"Tools:     {len(lc_tools)}")

    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
    agent = create_react_agent(llm, lc_tools)

    async with UndoLogSession(org_id=org_id) as session:
        _current_session.set(session)
        print(f"Session:   {session.session_id}\n")

        messages = await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        "A premium customer named Alice (cust_42) reports she "
                        "cannot access the enterprise dashboard. Look up her info, "
                        "create a medium priority support ticket, send an "
                        "acknowledgment email, and escalate the case."
                    )
                ]
            }
        )

        print(f"\nFinal: {messages['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
