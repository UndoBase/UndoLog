"""
Semantic Kernel agent using UndoLog for exactly-once tool execution.

Demonstrates undolog working with Microsoft's Semantic Kernel framework.

Requires ``semantic-kernel`` to be installed
(``pip install semantic-kernel``).

Usage
-----
::

    export OPENAI_API_KEY=sk-...
    python examples/semantic-kernel-support-agent/semantic_kernel_agent.py
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
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
    from semantic_kernel.functions import KernelFunctionFromMethod
    from semantic_kernel.kernel import Kernel
except ImportError:
    log.warning("semantic-kernel not installed. Install with: pip install semantic-kernel")
    Kernel = None  # type: ignore[assignment]
    OpenAIChatCompletion = None  # type: ignore[assignment]
    KernelFunctionFromMethod = None  # type: ignore[assignment]


_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
    type(None): "null",
}


def _to_sk_type(python_type: type) -> str:
    """Convert a Python type to a Semantic Kernel parameter type string.

    Parameters
    ----------
    python_type : type
        The Python type to convert.

    Returns
    -------
    str
        SK type name (``"string"``, ``"integer"``, etc.).
    """
    origin = getattr(python_type, "__origin__", None)
    if origin is not None:
        return "string"
    return _TYPE_MAP.get(python_type, "string")


async def _make_sk_function(
    name: str,
    raw_fn: Callable[..., Coroutine[Any, Any, Any]],
) -> KernelFunctionFromMethod:  # type: ignore[type-arg]
    """Build a Semantic Kernel ``KernelFunctionFromMethod`` from a raw UndoLog tool.

    Parameters
    ----------
    name : str
        Tool name.
    raw_fn : callable
        The UndoLog-decorated async function.

    Returns
    -------
    KernelFunctionFromMethod
        SK-compatible function that injects ``_session`` from the context var.
    """
    hints = get_type_hints(raw_fn)
    params: list[dict[str, Any]] = []
    for param_name, param_type in hints.items():
        if param_name in ("return", "_session"):
            continue
        params.append({"name": param_name, "type": _to_sk_type(param_type)})

    description = (raw_fn.__doc__ or "").strip().split("\n")[0] if raw_fn.__doc__ else name

    async def fn_wrapper(**kwargs: Any) -> str:
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

    return KernelFunctionFromMethod(
        function_name=name,
        method=fn_wrapper,
        description=description,
        parameters=params,
    )


async def main() -> None:
    """Run the Semantic Kernel agent inside an UndoLog session.

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
    if Kernel is None:
        log.error("semantic-kernel package required. Install with: pip install semantic-kernel")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("Set OPENAI_API_KEY environment variable")
        return

    org_id = os.environ.get("UNDOLOG_ORG_ID", "org_demo")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")

    kernel = Kernel()

    ai_service = OpenAIChatCompletion(
        ai_model_id=model,
        api_key=api_key,
        endpoint=base_url,
    )
    kernel.add_service(ai_service)

    registry = get_tool_registry()
    for name, raw_fn in registry.items():
        sk_fn = await _make_sk_function(name, raw_fn)
        kernel.add_function(plugin_name="support", function=sk_fn)

    prompt = (
        "A premium customer named Alice (cust_42) reports she cannot "
        "access the enterprise dashboard. Look up her info, create a "
        "medium priority support ticket, and send an acknowledgment email."
    )

    async with UndoLogSession(org_id=org_id) as session:
        _current_session.set(session)
        log.info("Session: %s", session.session_id)
        log.info("Org:     %s", session.org_id)

        result = await kernel.invoke_prompt(
            function_name="chat",
            plugin_name="support",
            prompt=prompt,
        )
        log.info("Final: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
