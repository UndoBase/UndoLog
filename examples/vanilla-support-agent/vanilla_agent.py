"""
Vanilla asyncio agent using UndoLog for exactly-once tool execution.

Demonstrates undolog working with a plain Python async loop and the OpenAI
client directly, without any agent framework.

Usage
-----
::

    export OPENAI_API_KEY=sk-...
    python examples/vanilla-support-agent/vanilla_agent.py
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

import openai

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


_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
    type(None): "null",
}


def _to_json_schema_type(python_type: type) -> dict[str, Any]:
    """Convert a Python type hint to an OpenAI JSON schema property.

    Parameters
    ----------
    python_type : type
        The Python type to convert (e.g. ``str``, ``int``, ``list[str]``).

    Returns
    -------
    dict
        A JSON schema fragment for the property (e.g. ``{"type": "string"}``).
    """
    origin = getattr(python_type, "__origin__", None)
    if origin is None:
        type_name = _TYPE_MAP.get(python_type, "string")
        return {"type": type_name}

    if origin is list:
        item_type = python_type.__args__[0] if python_type.__args__ else str
        return {"type": "array", "items": _to_json_schema_type(item_type)}

    if origin is dict:
        return {"type": "object"}

    return {"type": "string"}


def _build_openai_tool_def(
    tool_name: str, raw_fn: Callable[..., Coroutine[Any, Any, Any]]
) -> dict[str, Any]:
    """Build an OpenAI tool definition from a raw UndoLog tool.

    Parameters
    ----------
    tool_name : str
        Name of the tool.
    raw_fn : callable
        The UndoLog-decorated async function.

    Returns
    -------
    dict
        An OpenAI-compatible tool definition with ``type`` and ``function``
        fields.
    """
    hints = get_type_hints(raw_fn)
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []

    for param_name, param_type in hints.items():
        if param_name in ("return", "_session"):
            continue
        properties[param_name] = _to_json_schema_type(param_type)
        required.append(param_name)

    description = (raw_fn.__doc__ or "").strip().split("\n")[0] if raw_fn.__doc__ else tool_name

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


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


async def _execute_tool_call(
    tool_call: dict[str, Any],
    registry: dict[str, Callable[..., Coroutine[Any, Any, Any]]],
) -> dict[str, Any]:
    """Execute one OpenAI tool call and return the result.

    Parameters
    ----------
    tool_call : dict
        The LLM-generated tool call with ``name`` and ``arguments`` (JSON string).
    registry : dict
        The tool registry mapping names to functions.

    Returns
    -------
    dict
        A tool message result with ``role``, ``tool_call_id``, and ``content``.
    """
    tool_name = tool_call["function"]["name"]
    tool_fn = registry.get(tool_name)
    if tool_fn is None:
        return {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": json.dumps({"error": f"Unknown tool: {tool_name}"}),
        }

    try:
        args = json.loads(tool_call["function"]["arguments"])
        result = await tool_fn(**args, _session=_current_session.get())
        return {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": json.dumps(result, indent=2),
        }
    except AwaitingApprovalError as exc:
        log.info(
            "IRREVERSIBLE tool requires approval: approval_id=%s tool=%s",
            exc.approval_id,
            exc.tool_name,
        )
        return {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": json.dumps(
                {
                    "status": "requires_approval",
                    "approval_id": exc.approval_id,
                    "tool_name": exc.tool_name,
                    "message": f"Tool '{exc.tool_name}' requires human approval (approval_id={exc.approval_id}).",
                }
            ),
        }
    except Exception as exc:
        log.error("Tool %s failed: %s", tool_name, exc)
        return {
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": json.dumps({"error": f"Tool {tool_name} failed: {exc}"}),
        }


async def main() -> None:
    """Run the vanilla asyncio agent inside an UndoLog session.

    Expects
    -------
    OPENAI_API_KEY : str
        API key for the OpenAI-compatible LLM provider.
    UNDOLOG_ORG_ID : str
        Organisation identifier (default ``org_demo``).
    OPENAI_MODEL : str
        Model name (default ``gpt-4o``).
    OPENAI_BASE_URL : str
        Base URL for the OpenAI-compatible API (default OpenAI).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("Set OPENAI_API_KEY environment variable")
        return

    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")

    client = openai.AsyncClient(api_key=api_key, base_url=base_url)

    registry = get_tool_registry()
    tool_defs: list[dict[str, Any]] = [
        _build_openai_tool_def(name, fn) for name, fn in registry.items()
    ]

    prompt = (
        "A premium customer named Alice (cust_42) reports she cannot "
        "access the enterprise dashboard. Look up her info, create a "
        "medium priority support ticket, and send an acknowledgment email."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    async with UndoLogSession(org_id=org_id) as session:
        _current_session.set(session)
        log.info("Session: %s", session.session_id)
        log.info("Org:     %s", org_id)

        while True:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tool_defs or None,
            )

            choice = response.choices[0]
            msg = choice.message

            if not msg.tool_calls:
                log.info("Final: %s", msg.content or "")
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                tool_call_dict = {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                result = await _execute_tool_call(tool_call_dict, registry)
                messages.append(result)

    log.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
