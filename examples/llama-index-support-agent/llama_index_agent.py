"""
LlamaIndex agent using UndoLog for exactly-once tool execution.

Demonstrates undolog working with the LlamaIndex data framework via
``FunctionTool`` wrappers.  Proves ADR 0006's claim that undolog is
framework-agnostic.

Requires ``llama-index`` to be installed (``pip install llama-index``).

Usage
-----
::

    export OPENAI_API_KEY=sk-...
    python examples/llama-index-support-agent/llama_index_agent.py
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import sys
from collections.abc import Callable, Coroutine
from typing import Any, get_type_hints

from undolog_sdk import AwaitingApprovalError
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
    from llama_index.core.agent import AgentRunner
    from llama_index.core.tools import FunctionTool
    from llama_index.llms.openai import OpenAI as OpenAILLM
except ImportError:
    log.warning("llama-index not installed. Install with: pip install llama-index")
    AgentRunner = None  # type: ignore[assignment]
    FunctionTool = None  # type: ignore[assignment]
    OpenAILLM = None  # type: ignore[assignment]


def _make_fn_tool(
    name: str,
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> FunctionTool:  # type: ignore[type-arg]
    """Build a LlamaIndex ``FunctionTool`` from a raw UndoLog tool.

    Parameters
    ----------
    name : str
        Tool name.
    raw_fn : callable
        The UndoLog-decorated async function.

    Returns
    -------
    FunctionTool
        LlamaIndex-compatible tool that injects ``_session`` from the
        context var.
    """
    if FunctionTool is None:
        raise RuntimeError("llama-index package must be installed")

    hints = get_type_hints(raw_fn)
    schema_properties: dict[str, tuple[type, ...]] = {}
    for param_name, param_type in hints.items():
        if param_name in ("return", "_session"):
            continue
        schema_properties[param_name] = (param_type, ...)

    description = (raw_fn.__doc__ or "").strip().split("\n")[0] if raw_fn.__doc__ else name

    async def wrapper(**kwargs: Any) -> str:
        session = _current_session.get()
        if session is None:
            raise RuntimeError(f"Tool '{name}' called outside an active UndoLog session")
        try:
            result = await raw_fn(**kwargs, _session=session)
            return json.dumps(result, indent=2)
        except AwaitingApprovalError as exc:
            log.info(
                "IRREVERSIBLE tool requires approval: approval_id=%s tool=%s",
                exc.approval_id,
                exc.tool_name,
            )
            return json.dumps(
                {
                    "status": "requires_approval",
                    "approval_id": exc.approval_id,
                    "tool_name": exc.tool_name,
                }
            )
        except Exception as exc:
            log.error("Tool %s failed: %s", name, exc)
            return json.dumps({"error": f"Tool {name} failed: {exc}"})

    return FunctionTool.from_defaults(
        fn=wrapper,
        name=name,
        description=description,
    )


async def main() -> None:
    """Run the LlamaIndex agent inside an UndoLog session.

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
    if AgentRunner is None:
        log.error("llama-index package required. Install with: pip install llama-index")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("Set OPENAI_API_KEY environment variable")
        return

    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")

    llm = OpenAILLM(
        model=model,
        api_key=api_key,
        api_base=base_url,
    )

    raw_tools = get_tool_registry()
    tools = [_make_fn_tool(name, fn) for name, fn in raw_tools.items()]

    agent = AgentRunner.from_llm(
        llm=llm,
        tools=tools,
        verbose=False,
    )

    prompt = (
        "A premium customer named Alice (cust_42) reports she cannot "
        "access the enterprise dashboard. Look up her info, create a "
        "medium priority support ticket, and send an acknowledgment email."
    )

    async with UndoLogSession(org_id=org_id) as session:
        _current_session.set(session)
        log.info("Session: %s", session.session_id)
        log.info("Org:     %s", org_id)

        response = await agent.aquery(prompt)
        log.info("Final: %s", response)


if __name__ == "__main__":
    asyncio.run(main())
