"""Decorator for intercepting tool calls through the UndoLog effect system.

Usage::

    from undolog_sdk import undolog_tool, ToolTier
    from undolog_sdk.session import UndoLogSession
    from undolog_sdk.tier import CompensationDescriptor

    @undolog_tool(tier=ToolTier.SAFE)
    async def search_web(query: str) -> str:
        return f"results for {query}"

    @undolog_tool(
        tier=ToolTier.COMPENSABLE,
        compensation=CompensationDescriptor.new("undo_send_email"),
    )
    async def send_email(to: str, subject: str) -> dict:
        return {"status": "sent"}

    @undolog_tool(tier=ToolTier.IRREVERSIBLE)
    async def delete_database(db_name: str) -> dict:
        return {"status": "deleted"}
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from undolog_sdk.client import UndoLogClient, _get_default_client
from undolog_sdk.context import get_current_session
from undolog_sdk.session import UndoLogSession
from undolog_sdk.tier import CompensationDescriptor, ToolTier

log = logging.getLogger(__name__)


def undolog_tool(
    tier: ToolTier,
    *,
    compensation: CompensationDescriptor | None = None,
    client: UndoLogClient | None = None,
    session_param: str = "_session",
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator that wraps an async function with UndoLog interception.

    Args:
        tier: The tool's classification (Safe, Compensable, or Irreversible).
        compensation: Required if tier is ``Compensable``.
        client: An ``UndoLogClient`` instance. If omitted, a module-level
            default client is used (lazily initialised from environment).
        session_param: Name of the keyword argument that receives the
            ``UndoLogSession`` at call time. If not provided, the
            decorator falls back to the session set by ``run_with_session``.

    Raises:
        ValueError: If ``Compensable`` tier is used without a compensation
            descriptor.
        RuntimeError: If no session is found via explicit parameter or
            context var.
        AwaitingApprovalError: If the intercept outcome requires human
            approval and execution is suspended.

    Flow before function execution:
        1. Compute the ``call_signature`` from session, step, name, and args.
        2. Call ``UndoLogClient.intercept(...)``.
        3. Branch on the outcome…

    Outcomes:
        ``Execute``
            Run the wrapped function normally, then call ``commit`` or ``fail``.
        ``Replay``
            Return the cached value without entering the function body,
            unwrapped from the ``ToolResult`` envelope so the caller
            receives the tool's natural return type: identical to the
            Execute outcome.
        ``AwaitingApproval``
            Raise ``AwaitingApprovalError`` with the approval identifier -
            the function body is **not** executed.
    """
    if tier is ToolTier.COMPENSABLE and compensation is None:
        raise ValueError(
            "Compensable tools require a compensation descriptor. "
            "Pass compensation=CompensationDescriptor.new(...)"
        )

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        tool_name = func.__name__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            session: UndoLogSession | None = kwargs.pop(session_param, None)
            if session is None:
                session = get_current_session()
            if session is None:
                raise RuntimeError(
                    f"Tool '{tool_name}' requires a session. "
                    f"Pass {session_param}=session to the call or wrap "
                    f"with run_with_session(session)."
                )

            cl = client if client is not None else _get_default_client()

            # Safe tier: bypass the proxy entirely - execute freely.
            if tier is ToolTier.SAFE:
                log.debug(
                    "tool_execute tool=%s session=%s (safe tier, bypass proxy)",
                    tool_name,
                    session.session_id,
                )
                return await func(*args, **kwargs)

            step_index = session.next_step()

            # Build the args dict from all parameters.
            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            call_args = {k: v for k, v in bound.arguments.items() if k != session_param}

            # Non-Safe tiers: send the tool call to the proxy for
            # interception. The proxy decides whether to execute it
            # fresh, return a cached result (replay), or request human
            # approval (irreversible).
            response = await cl.intercept(
                org_id=session.org_id,
                session_id=session.session_id,
                tool_name=tool_name,
                step_index=step_index,
                args=call_args,
            )

            if response.outcome == "Replay":
                log.info(
                    "tool_replay tool=%s step=%d session=%s",
                    tool_name,
                    step_index,
                    session.session_id,
                )
                return _unwrap_replay_result(response.cached_result)

            if response.outcome == "AwaitingApproval":
                log.warning(
                    "approval_required tool=%s approval_id=%s session=%s",
                    tool_name,
                    response.approval_id,
                    session.session_id,
                )
                raise AwaitingApprovalError(
                    approval_id=response.approval_id or "",
                    tool_name=tool_name,
                    step_index=step_index,
                )

            # outcome == "Execute": run the function body
            log.debug(
                "tool_execute tool=%s step=%d session=%s",
                tool_name,
                step_index,
                session.session_id,
            )
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                if response.effect_id:
                    log.warning(
                        "tool_failed tool=%s session=%s effect_id=%s error=%s",
                        tool_name,
                        session.session_id,
                        response.effect_id,
                        exc,
                    )
                    try:
                        await cl.fail(
                            org_id=session.org_id,
                            session_id=session.session_id,
                            effect_id=response.effect_id,
                            error=str(exc),
                        )
                    except Exception:
                        raise exc from None
                raise

            if response.effect_id:
                log.info(
                    "tool_committed session=%s effect_id=%s",
                    session.session_id,
                    response.effect_id,
                )
                await cl.commit(
                    org_id=session.org_id,
                    session_id=session.session_id,
                    effect_id=response.effect_id,
                    result={"success": True, "output": result},
                )

            return result

        wrapper._undolog_tier = tier  # type: ignore[attr-defined]
        wrapper._undolog_compensation = compensation  # type: ignore[attr-defined]
        wrapper._undolog_tool_name = tool_name  # type: ignore[attr-defined]

        return wrapper

    return decorator


def _unwrap_replay_result(cached: dict[str, Any] | None) -> Any:
    """Extract the tool's natural return value from a Replay cache entry.

    The proxy caches results as a ``ToolResult`` envelope of the form
    ``{"success": bool, "output": <tool result>, "error": str,
    "duration_ms": int}``. The envelope is a transport detail: on the
    Execute path the caller receives the raw function result, so the
    Replay path must return the same shape.

    Args:
        cached: The ``cached_result`` field of a Replay ``InterceptResponse``.

    Returns:
        The unwrapped tool result. If the envelope has an ``output``
        key, the value under that key is returned. Dicts without an
        ``output`` key pass through unchanged so results that do not
        use the envelope format are returned as-is.
    """
    if isinstance(cached, dict) and "output" in cached:
        return cached["output"]
    return cached


class AwaitingApprovalError(RuntimeError):
    """Raised when an Irreversible tool call requires human approval.

    The caller should surface the ``approval_id`` to the user and refrain
    from retrying until the approval is resolved.
    """

    def __init__(
        self,
        approval_id: str,
        tool_name: str = "",
        step_index: int = 0,
    ) -> None:
        self.approval_id = approval_id
        self.tool_name = tool_name
        self.step_index = step_index
        super().__init__(
            f"Tool '{tool_name}' (step {step_index}) requires approval. "
            f"approval_id={approval_id}"
        )
