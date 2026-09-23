"""Async HTTP client for the UndoLog MCP Proxy.

The ``UndoLogClient`` communicates with the Go proxy (or any UndoLog-compatible
service) to intercept tool calls, commit execution results, and report failures.

Environment configuration:
    ``UNDOLOG_PROXY_URL`` - base URL of the UndoLog proxy (default: ``http://localhost:8080``).
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import types
from dataclasses import dataclass
from typing import Any, cast

import httpx

from undolog_sdk.errors import (
    AuthenticationError,
    ConnectionError,
    ServerError,
    TimeoutError,
)

log = logging.getLogger(__name__)

_DEFAULT_CLIENT: UndoLogClient | None = None
_background_tasks: set[asyncio.Task[None]] = set()


def _get_default_client() -> UndoLogClient:
    """Return (and lazily initialise) the module-level default ``UndoLogClient``.

    The client is created once and reused so that connection pooling and
    header defaults are shared across all decorated tools that do not
    specify an explicit client.

    Returns:
        The lazily-initialised default client instance.
    """
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = UndoLogClient()
    return _DEFAULT_CLIENT


def _close_default_client() -> None:
    """Close the default client if it was created.

    Called on session exit and interpreter shutdown to prevent
    connection pool leaks in long-running services.
    """
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is not None:
        client = _DEFAULT_CLIENT
        _DEFAULT_CLIENT = None
        if not client._http.is_closed:
            log.debug("closing default UndoLogClient")
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(client.aclose())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            except RuntimeError:
                # No running loop (e.g. interpreter shutdown).
                log.debug("cannot close default client: no running event loop")


atexit.register(_close_default_client)


def _safe_request_url(exc: Exception) -> str:
    """Extract the request URL from an httpx exception, if available."""
    try:
        req = exc.request  # type: ignore[attr-defined]
    except (RuntimeError, AttributeError):
        return ""
    return str(req.url) if req is not None else ""


@dataclass
class InterceptResponse:
    """Decision from the UndoLog engine for one intercepted tool call.

    Returned by :meth:`UndoLogClient.intercept` and consumed by the
    ``@undolog_tool`` decorator to decide how to route execution.

    Fields are populated depending on the ``outcome``:

    =================== ========== ========== ================
    Field                Execute    Replay     AwaitingApproval
    =================== ========== ========== ================
    ``effect_id``        ✓          ✓          -
    ``approval_id``      -          -          ✓
    ``cached_result``    -          ✓          -
    =================== ========== ========== ================
    """

    outcome: str
    """One of ``Execute``, ``Replay``, ``AwaitingApproval``."""

    effect_id: str | None = None
    """Effect log entry identifier. Present for Execute and Replay outcomes."""

    approval_id: str | None = None
    """Approval request identifier: present only for AwaitingApproval."""

    cached_result: dict[str, Any] | None = None
    """Cached tool result: present only for Replay."""


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
            AuthenticationError: On 401/403 responses.
            ServerError: On 5xx responses.
            TimeoutError: On request timeouts.
            ConnectionError: On connection failures.
        """
        try:
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
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"Intercept request timed out: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to proxy: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"HTTP error during intercept: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed: {resp.status_code}",
                status_code=resp.status_code,
            )
        if resp.status_code >= 500:
            raise ServerError(
                f"Proxy server error: {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
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
        if status not in outcome_map:
            raise ValueError(f"Unexpected proxy status: {status!r}")
        outcome = outcome_map[status]
        log.info(
            "intercept outcome=%s tool=%s step=%d session=%s",
            outcome,
            tool_name,
            step_index,
            session_id,
        )
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
        inline and this method is a safe no-op (returns empty dict).

        This method exists for API parity with a future direct-engine HTTP
        surface.  Against the current proxy it always returns ``{}``.

        Returns:
            Empty dict (the proxy commits inline via ``POST /mcp/tool_call``).

        Raises:
            AuthenticationError: On 401/403 responses.
            ServerError: On 5xx responses.
            TimeoutError: On request timeouts.
            ConnectionError: On connection failures.
        """
        url = f"/effects/{effect_id}/commit"
        body = {"session_id": session_id, "result": result}
        try:
            resp = await self._http.put(
                url,
                headers=self._headers(org_id, session_id),
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"Commit request timed out: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to proxy: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"HTTP error during commit: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        if resp.status_code == 404:
            return {}
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed: {resp.status_code}",
                status_code=resp.status_code,
            )
        if resp.status_code >= 500:
            raise ServerError(
                f"Proxy server error: {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
            )
        resp.raise_for_status()
        log.info(
            "tool_committed session=%s effect_id=%s",
            session_id,
            effect_id,
        )
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
        failure inline and this method is a safe no-op (returns empty dict).

        This method exists for API parity with a future direct-engine HTTP
        surface.  Against the current proxy it always returns ``{}``.

        Returns:
            Empty dict (the proxy handles failure inline via ``POST /mcp/tool_call``).

        Raises:
            AuthenticationError: On 401/403 responses.
            ServerError: On 5xx responses.
            TimeoutError: On request timeouts.
            ConnectionError: On connection failures.
        """
        url = f"/effects/{effect_id}/fail"
        body = {"session_id": session_id, "error": error}
        try:
            resp = await self._http.put(
                url,
                headers=self._headers(org_id, session_id),
                json=body,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"Fail request timed out: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to proxy: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"HTTP error during fail: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        if resp.status_code == 404:
            return {}
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed: {resp.status_code}",
                status_code=resp.status_code,
            )
        if resp.status_code >= 500:
            raise ServerError(
                f"Proxy server error: {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
            )
        resp.raise_for_status()
        log.warning(
            "tool_failed session=%s effect_id=%s error=%s",
            session_id,
            effect_id,
            error,
        )
        return cast(dict[str, Any], resp.json())

    async def approve(
        self,
        org_id: str,
        approval_id: str,
    ) -> dict[str, Any]:
        """Approve a pending approval request and resume the session.

        Args:
            org_id: Organisation identifier (advisory; the proxy derives org
                from ``X-Api-Key``).
            approval_id: Approval request identifier from ``AwaitingApprovalError``.

        Returns:
            Server response with ``status``, ``approval_id``, ``effect_id``,
            ``execution``, and ``result`` fields.

        Raises:
            AuthenticationError: On 401/403 responses.
            ServerError: On 5xx responses.
            TimeoutError: On request timeouts.
            ConnectionError: On connection failures.
        """
        url = f"/approvals/{approval_id}/approve"
        try:
            resp = await self._http.post(
                url,
                headers=self._headers(org_id, ""),
                json={},
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"Approve request timed out: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to proxy: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"HTTP error during approve: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed: {resp.status_code}",
                status_code=resp.status_code,
            )
        if resp.status_code >= 500:
            raise ServerError(
                f"Proxy server error: {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
            )
        resp.raise_for_status()
        log.info(
            "approval_approved approval_id=%s",
            approval_id,
        )
        return cast(dict[str, Any], resp.json())

    async def reject(
        self,
        org_id: str,
        approval_id: str,
    ) -> dict[str, Any]:
        """Reject a pending approval request and halt the session.

        Args:
            org_id: Organisation identifier (advisory; the proxy derives org
                from ``X-Api-Key``).
            approval_id: Approval request identifier from ``AwaitingApprovalError``.

        Returns:
            Server response with ``status`` and ``approval_id`` fields.

        Raises:
            AuthenticationError: On 401/403 responses.
            ServerError: On 5xx responses.
            TimeoutError: On request timeouts.
            ConnectionError: On connection failures.
        """
        url = f"/approvals/{approval_id}/reject"
        try:
            resp = await self._http.post(
                url,
                headers=self._headers(org_id, ""),
                json={},
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"Reject request timed out: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to proxy: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"HTTP error during reject: {exc}",
                url=_safe_request_url(exc),
            ) from exc
        if resp.status_code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed: {resp.status_code}",
                status_code=resp.status_code,
            )
        if resp.status_code >= 500:
            raise ServerError(
                f"Proxy server error: {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
            )
        resp.raise_for_status()
        log.info(
            "approval_rejected approval_id=%s",
            approval_id,
        )
        return cast(dict[str, Any], resp.json())

    async def aclose(self) -> None:
        """Close the underlying HTTP client session."""
        await self._http.aclose()

    async def __aenter__(self) -> UndoLogClient:
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit the async context manager, closing the HTTP client."""
        await self.aclose()

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
