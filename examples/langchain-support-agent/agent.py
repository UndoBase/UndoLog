"""
LangChain agent using UndoLog for exactly-once tool execution.

The agent drives a customer-support workflow through four UndoLog-wrapped
tools (SAFE / COMPENSABLE / IRREVERSIBLE).  Every tool call is recorded
in the effect log so that crashes, replays, and rollbacks are handled
deterministically.

The raw ``@undolog_tool`` functions require a ``_session`` kwarg that the
LLM does not know about.  This module wraps each tool with a
``StructuredTool`` that injects ``_session`` from a context variable,
keeping the LLM schema clean.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections.abc import Callable, Coroutine
from typing import Any, get_type_hints

from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, create_model

from undolog_sdk.session import UndoLogSession

from tools import get_tool_registry

_current_session: contextvars.ContextVar[UndoLogSession | None] = contextvars.ContextVar(
    "undolog_session", default=None
)


def _make_wrapper(
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


def _make_args_schema(
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> type[BaseModel]:
    """Build a Pydantic model from the raw function's type hints.

    Parameters
    ----------
    raw_fn : callable
        The UndoLog-decorated async function whose type hints define
        the input schema.

    Returns
    -------
    type[BaseModel]
        Pydantic model with all parameters except ``_session`` and ``return``.
    """
    hints = get_type_hints(raw_fn)
    fields: dict[str, tuple[type, Any]] = {}
    for param_name, param_type in hints.items():
        if param_name in ("return", "_session"):
            continue
        fields[param_name] = (param_type, ...)
    model_name = f"{raw_fn.__name__}Args"
    return create_model(model_name, **fields)


def _make_lc_tool(
    name: str,
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> StructuredTool:
    """Build a LangChain ``StructuredTool`` from a raw UndoLog tool.

    Parameters
    ----------
    name : str
        Tool name exposed to the LLM.
    raw_fn : callable
        The UndoLog-decorated async function.

    Returns
    -------
    StructuredTool
        A LangChain-compatible tool with proper JSON schema and session
        injection.
    """
    description = (raw_fn.__doc__ or "").strip().split("\n")[0] if raw_fn.__doc__ else name
    return StructuredTool.from_function(
        name=name,
        description=description,
        args_schema=_make_args_schema(raw_fn),
        func=None,
        coroutine=_make_wrapper(name, raw_fn),
    )


async def main() -> None:
    """Run the LangChain agent inside an UndoLog session.

    Expects
    -------
    UNDOLOG_PROXY_URL : str
        HTTP address of the UndoLog proxy (default ``http://localhost:8080``).
    OPENAI_API_KEY : str
        API key for the OpenAI-compatible LLM provider.
    UNDOLOG_ORG_ID : str
        Organisation identifier (default ``org_demo``).

    Notes
    -----
    The agent uses ``gpt-4o`` by default.  Override with ``OPENAI_MODEL``
    and ``OPENAI_BASE_URL`` to use any OpenAI-compatible provider.
    """
    proxy_url = os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        print("ERROR: Set OPENAI_API_KEY environment variable")
        return

    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")

    print(f"Connecting to UndoLog proxy at {proxy_url} ...")

    raw_tools = get_tool_registry()
    tools: list[StructuredTool] = [_make_lc_tool(name, fn) for name, fn in raw_tools.items()]

    llm = ChatOpenAI(model=model, api_key=openai_api_key, base_url=base_url)
    agent = create_react_agent(llm, tools)

    async with UndoLogSession(org_id=org_id) as session:
        _current_session.set(session)
        print(f"\nSession: {session.session_id}")
        print(f"Org:      {org_id}\n")

        messages = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "A premium customer named Alice (cust_42) reports she "
                            "can't access the enterprise dashboard. Look up her info, "
                            "create a support ticket, and send an acknowledgment email."
                        ),
                    }
                ]
            }
        )

        print(f"\nAgent response: {messages['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
