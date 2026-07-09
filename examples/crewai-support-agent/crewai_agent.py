"""
CrewAI agent using UndoLog for exactly-once tool execution.

Demonstrates undolog working with the CrewAI multi-agent framework via
LangChain ``StructuredTool`` wrappers.

Requires ``crewai`` to be installed (``pip install crewai``).

Usage
-----
::

    export OPENAI_API_KEY=sk-...
    python examples/crewai-support-agent/crewai_agent.py
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import sys
from collections.abc import Callable, Coroutine
from typing import Any, get_type_hints

from pydantic import BaseModel, create_model

from undolog_sdk.session import UndoLogSession

_examples_root = os.path.join(os.path.dirname(__file__), "..")
if _examples_root not in sys.path:
    sys.path.insert(0, _examples_root)

from example_tools import get_tool_registry  # noqa: E402  -- sys.path insertion above

log = logging.getLogger(__name__)

_current_session: contextvars.ContextVar[UndoLogSession | None] = contextvars.ContextVar(
    "undolog_session", default=None
)

try:
    from crewai import Agent, Crew, Task
    from langchain_core.tools import StructuredTool
except ImportError:
    log.warning("crewai or langchain_core not installed. Install with: pip install crewai")
    Agent = None  # type: ignore[assignment]
    Crew = None  # type: ignore[assignment]
    Task = None  # type: ignore[assignment]
    StructuredTool = None  # type: ignore[assignment]


def _make_args_schema(
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> type[BaseModel]:
    """Build a Pydantic model from the raw function's type hints.

    Parameters
    ----------
    raw_fn : callable
        The UndoLog-decorated async function.

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

    async def wrapper(**kwargs: Any) -> Any:
        session = _current_session.get()
        if session is None:
            raise RuntimeError(f"Tool '{name}' called outside an active UndoLog session")
        return await raw_fn(**kwargs, _session=session)

    return wrapper


def _build_lc_tools() -> list[StructuredTool]:  # type: ignore[type-arg]
    """Build LangChain ``StructuredTool`` wrappers for the tool registry.

    Returns
    -------
    list[StructuredTool]
        LangChain-compatible tool wrappers with session injection.
    """
    if StructuredTool is None:
        raise RuntimeError("crewai and langchain_core must be installed")

    registry = get_tool_registry()
    tools: list[StructuredTool] = []
    for name, raw_fn in registry.items():
        description = (raw_fn.__doc__ or "").strip().split("\n")[0] if raw_fn.__doc__ else name
        tool = StructuredTool.from_function(
            name=name,
            description=description,
            args_schema=_make_args_schema(raw_fn),
            func=None,
            coroutine=_make_wrapper(name, raw_fn),
        )
        tools.append(tool)
    return tools


async def main() -> None:
    """Run the CrewAI agent inside an UndoLog session.

    Expects
    -------
    OPENAI_API_KEY : str
        API key for the OpenAI-compatible LLM provider.
    UNDOLOG_ORG_ID : str
        Organisation identifier (default ``org_demo``).
    OPENAI_MODEL : str
        Model name (default ``gpt-4o``).
    OPENAI_BASE_URL : str
        Base URL for the OpenAI-compatible API.
    """
    if Agent is None:
        log.error("crewai package required. Install with: pip install crewai")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("Set OPENAI_API_KEY environment variable")
        return

    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")

    llm_config: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
    }
    if base_url:
        llm_config["base_url"] = base_url

    tools = _build_lc_tools()

    agent = Agent(
        role="Customer Support Agent",
        goal="Resolve customer issues by looking up information, creating tickets, and sending emails.",
        backstory="You are an experienced customer support agent handling enterprise tickets.",
        tools=tools,
        llm=llm_config,
        allow_delegation=False,
    )

    task = Task(
        description=(
            "A premium customer named Alice (cust_42) reports she cannot "
            "access the enterprise dashboard. Look up her info, create a "
            "medium priority support ticket, and send an acknowledgment email."
        ),
        expected_output="Summary of actions taken and the resolution status.",
        agent=agent,
    )

    crew = Crew(agents=[agent], tasks=[task], verbose=False)

    async with UndoLogSession(org_id=org_id) as session:
        _current_session.set(session)
        log.info("Session: %s", session.session_id)
        log.info("Org:     %s", org_id)
        result = await crew.kickoff_async()
        log.info("Final: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
