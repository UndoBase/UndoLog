"""Async HTTP client for the UndoLog MCP Proxy.

The ``UndoLogClient`` communicates with the Go proxy (or any UndoLog-compatible
service) to intercept tool calls, commit execution results, and report failures.

Environment configuration:
    ``UNDOLOG_PROXY_URL`` - base URL of the UndoLog proxy (default: ``http://localhost:8080``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast

import httpx


@dataclass
class InterceptResponse:
    """Decision from the UndoLog engine for one intercepted tool call.

    Returned by :meth:`UndoLogClient.intercept` and consumed by the
    ``@undolog_tool`` decorator to decide how to route execution.

    Fields are populated depending on the ``outcome``:

    =================== ========== ========== ================
    Field                Execute    Replay     AwaitingApproval
    =================== ========== ========== ================
    ``effect_id``        ✓          ✓          ✓
    ``approval_id``      -          -          ✓
    ``cached_result``    -          ✓          -
    =================== ========== ========== ================
    """

    outcome: str
    """One of ``Execute``, ``Replay``, ``AwaitingApproval``."""

    effect_id: str | None = None
    """Effect log entry identifier - present for all outcomes."""

    approval_id: str | None = None
    """Approval request identifier - present only for AwaitingApproval."""

    cached_result: dict[str, Any] | None = None
    """Cached tool result - present only for Replay."""


def _default_proxy_url() -> str:
    """Return the UndoLog proxy base URL from the environment.

    Falls back to ``http://localhost:8080`` when the environment variable
    ``UNDOLOG_PROXY_URL`` is not set.
    """
    return os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


class UndoLogClient:
    """Async HTTP client for the UndoLog MCP Proxy.

    Usage::

        client = UndoLogClient()
        response = await client.intercept(
            org_id="org-abc",
            session_id="...",
            tool_name="transfer_funds",
            step_index=3,
            args={"to": "bob", "amount": 100},
        )
        if response.outcome == "Execute":
            result = await my_tool(**args)
            await client.commit(response.effect_id, result)

    Environment configuration:
        ``UNDOLOG_PROXY_URL`` - base URL of the UndoLog proxy (default: ``http://localhost:8080``).
        ``UNDOLOG_API_KEY`` - API key for proxy authentication.
    """

    def __init__(
        self,
        proxy_url: str | None = None,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (proxy_url or _default_proxy_url()).rstrip("/")
        self._api_key = api_key or os.environ.get("UNDOLOG_API_KEY", "")
        if http_client is not None:
            self._http = http_client
        else:
            self._http = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)

    async def intercept(
        self,
        org_id: str,
        session_id: str,
        tool_name: str,
        step_index: int,
        args: dict[str, Any],
    ) -> InterceptResponse:
        """Send a tool call to the proxy for interception.

        Args:
            org_id: Organisation scoping the call.
            session_id: Active session UUID.
            tool_name: Logical name of the tool.
            step_index: Call order within the session.
            args: Tool arguments as a JSON-compatible dict.

        Returns:
            An ``InterceptResponse`` indicating what to do next.

        Raises:
            httpx.HTTPStatusError: On proxy-level HTTP errors (4xx/5xx).
            httpx.RequestError: On connection or timeout errors.
        """
        resp = await self._http.post(
            "/mcp/tool_call",
            headers=self._headers(org_id, session_id),
            json={
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_version": "1.0.0",
                "step_index": step_index,
                "args": args,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        # Proxy returns {status, effect_id, result} directly.
        # Map proxy statuses to SDK outcome strings.
        status = body.get("status", "executed")
        outcome_map = {
            "executed": "Execute",
            "replayed": "Replay",
            "pending_approval": "AwaitingApproval",
        }
        outcome = outcome_map.get(status, status.capitalize())
        return InterceptResponse(
            outcome=outcome,
            effect_id=body.get("effect_id"),
            approval_id=body.get("approval_id"),
            cached_result=body.get("cached_result", body.get("result")),
        )

    async def commit(
        self,
        org_id: str,
        session_id: str,
        effect_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit a tool result for a proxy-originated call.

        For calls routed through ``POST /mcp/tool_call`` the proxy commits
        inline and this method is a safe no-op.  For direct engine access
        this sends ``PUT /effects/{effect_id}/commit`` to persist the result.

        Returns the server response body (or empty dict for the inline path).
        """
        url = f"/effects/{effect_id}/commit"
        body = {"session_id": session_id, "result": result}
        resp = await self._http.put(
            url,
            headers=self._headers(org_id, session_id),
            json=body,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())

    async def fail(
        self,
        org_id: str,
        session_id: str,
        effect_id: str,
        error: str,
    ) -> dict[str, Any]:
        """Mark an effect as failed and trigger compensation rollback.

        For calls routed through ``POST /mcp/tool_call`` the proxy handles
        failure inline and this method is a safe no-op.  For direct engine
        access this sends ``PUT /effects/{effect_id}/fail``.

        Returns the server response body (or empty dict for the inline path).
        """
        url = f"/effects/{effect_id}/fail"
        body = {"session_id": session_id, "error": error}
        resp = await self._http.put(
            url,
            headers=self._headers(org_id, session_id),
            json=body,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return cast(dict[str, Any], resp.json())

    async def aclose(self) -> None:
        """Close the underlying HTTP client session."""
        await self._http.aclose()

    def _headers(self, org_id: str, session_id: str) -> dict[str, str]:
        """Build tenant-scoped headers for every proxy request.

        ``X-Api-Key`` authenticates the organisation. ``X-UndoLog-Org-Id`` and
        ``X-UndoLog-Session-Id`` provide tenant isolation and session routing.
        """
        headers = {
            "X-UndoLog-Org-Id": org_id,
            "X-UndoLog-Session-Id": session_id,
        }
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        return headers
