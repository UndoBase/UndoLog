"""SSE dashboard consumer for UndoLog proxy lifecycle events.

Subscribes to the proxy's ``/events`` SSE endpoint and delivers parsed
``Event`` objects to a callback.  Supports multiple concurrent org
subscriptions through separate ``SSEConnection`` instances.

Usage::

    from sse_dashboard import SSEConnection, display_event

    conn = SSEConnection(org_id="org-alpha", api_key="dev-key")
    await conn.connect(callback=display_event)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("sse_dashboard")

_EVENT_LINE = "event:"
_DATA_LINE = "data:"
_HEARTBEAT = ": ping"


@dataclass
class Event:
    """A single SSE event from the proxy lifecycle stream.

    Maps directly to the Go ``sse.Event`` struct in
    ``services/undolog-proxy/internal/sse/broadcaster.go``.
    """

    type: str
    """Event type, one of ``effect_intercepted``, ``effect_committed``,
    ``effect_replayed``, ``effect_failed``, ``approval_required``,
    ``approval_approved``, ``approval_rejected``."""

    timestamp: str
    """ISO-8601 timestamp of when the event was emitted."""

    org_id: str
    """Tenant that owns the intercepted session."""

    session_id: str = ""
    """Affected session identifier."""

    effect_id: str = ""
    """Affected effect identifier."""

    approval_id: str = ""
    """Affected approval request identifier."""

    payload: dict[str, Any] = field(default_factory=dict)
    """Event-specific details."""


_EVENT_ICONS: dict[str, str] = {
    "effect_intercepted": "  >",
    "effect_executed": "  >",
    "effect_committed": "  OK",
    "effect_replayed": "  >>",
    "effect_failed": "  ERR",
    "approval_required": "  !",
    "approval_approved": "  YES",
    "approval_rejected": "  NO",
}


def _short_id(raw: str) -> str:
    """Truncate a UUID to 8 characters for more compact display."""
    return raw.split("-")[0] if "-" in raw else raw[:8]


def display_event(event: Event) -> None:
    """Render a single SSE event to the console in a compact one-line format.

    Parameters
    ----------
    event : Event
        The event to display.
    """
    icon = _EVENT_ICONS.get(event.type, "  ?")
    ts = event.timestamp[11:23] if len(event.timestamp) > 23 else event.timestamp
    session = _short_id(event.session_id) if event.session_id else ""
    effect = _short_id(event.effect_id) if event.effect_id else ""
    approval = _short_id(event.approval_id) if event.approval_id else ""

    parts = [f"[{ts}]", icon, f"org={event.org_id}"]
    if session:
        parts.append(f"sess={session}")
    if effect:
        parts.append(f"eff={effect}")
    if approval:
        parts.append(f"appr={approval}")

    print("  ".join(parts))


class SSEConnection:
    """A single SSE subscription to the proxy ``/events`` endpoint.

    The proxy's auth middleware resolves the ``X-Api-Key`` to an organisation
    UUID and sets ``X-Org-Id`` on the request before it reaches the SSE
    handler.  The SSE stream delivers events scoped to that organisation.

    Parameters
    ----------
    org_id : str
        Organisation label used as fallback when parsing events (the
        actual org_id in each event is set by the proxy's auth middleware
        from the API key lookup).
    api_key : str
        API key for proxy authentication (resolved to org UUID by
        the auth middleware).
    proxy_url : str
        Base URL of the UndoLog proxy.
    """

    def __init__(
        self,
        org_id: str,
        api_key: str,
        proxy_url: str = "http://localhost:8080",
    ) -> None:
        self._org_id = org_id
        self._api_key = api_key
        self._proxy_url = proxy_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def connect(
        self,
        callback: Callable[[Event], None] = display_event,
    ) -> None:
        """Open the SSE connection and dispatch events to *callback*.

        Runs indefinitely until the connection is closed or an error
        occurs.  Heartbeats (``: ping`` lines) are silently ignored.

        Parameters
        ----------
        callback : Callable[[Event], None]
            Called synchronously for each parsed ``Event``.  Defaults to
            ``display_event`` for console output.

        Raises
        ------
        httpx.RequestError
            If the proxy is unreachable or the connection fails.
        """
        headers = {
            "X-Api-Key": self._api_key,
            "Accept": "text/event-stream",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            self._client = client
            async with client.stream(
                "GET",
                f"{self._proxy_url}/events",
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                event_type = ""
                data_lines: list[str] = []

                async for line in resp.aiter_lines():
                    stripped = line.strip()

                    if stripped == _HEARTBEAT:
                        continue

                    if stripped.startswith(_EVENT_LINE):
                        event_type = stripped[len(_EVENT_LINE) :].strip()
                        continue

                    if stripped.startswith(_DATA_LINE):
                        data_lines.append(stripped[len(_DATA_LINE) :].strip())
                        continue

                    if stripped == "" and event_type and data_lines:
                        raw = "".join(data_lines)
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            log.warning("Failed to decode SSE data: %s", raw[:200])
                            event_type = ""
                            data_lines = []
                            continue

                        event = Event(
                            type=event_type,
                            timestamp=parsed.get("timestamp", ""),
                            org_id=parsed.get("org_id", self._org_id),
                            session_id=parsed.get("session_id", ""),
                            effect_id=parsed.get("effect_id", ""),
                            approval_id=parsed.get("approval_id", ""),
                            payload=parsed.get("payload", {}),
                        )
                        callback(event)
                        event_type = ""
                        data_lines = []

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def run_dashboard(
    org_ids: list[tuple[str, str]],
    proxy_url: str = "http://localhost:8080",
) -> None:
    """Run concurrent SSE dashboard subscriptions for multiple orgs.

    Each org appears as a separate column in the console output, making
    it easy to compare event streams side by side.

    Parameters
    ----------
    org_ids : list[tuple[str, str]]
        List of ``(org_id, api_key)`` pairs to subscribe to.
    proxy_url : str
        Base URL of the UndoLog proxy.
    """
    connections = [
        SSEConnection(org_id=org_id, api_key=api_key, proxy_url=proxy_url)
        for org_id, api_key in org_ids
    ]

    async def _run(label: str, conn: SSEConnection) -> None:
        print(f"  SSE connected : {label}")
        try:
            await conn.connect()
        except httpx.RequestError as exc:
            log.warning("SSE connection failed for %s: %s", label, exc)

    tasks = [
        asyncio.create_task(_run(org_id, conn)) for (org_id, _), conn in zip(org_ids, connections)
    ]

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("UndoLog : SSE Dashboard Consumer")
    print("─" * 50)
    print("  Connecting to proxy at http://localhost:8080")
    print("  Subscribed orgs: org-alpha (dev-key), org-beta (dev-key-2)")
    print("")

    asyncio.run(
        run_dashboard(
            org_ids=[("org-alpha", "dev-key"), ("org-beta", "dev-key-2")],
        )
    )
