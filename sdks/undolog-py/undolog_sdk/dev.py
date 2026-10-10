"""Local development server for the UndoLog Python SDK.

``undolog dev`` starts an HTTP server that speaks the same contract as
the UndoLog proxy, backed by a single SQLite file, so a tool annotation
can be exercised without Docker, PostgreSQL, or an API key::

    $ undolog dev
    $ undolog dev --irreversible 'delete_*' --db ./journal.db

The server listens on ``http://127.0.0.1:8080``, and
``UndoLogClient()`` dials ``http://localhost:8080`` when
``UNDOLOG_PROXY_URL`` is unset. Both names are loopback, so a
decorated tool reaches the local server the moment it is running,
with no configuration on either side.

What the local server does:
    Journals every intercepted call in SQLite, replays committed results
    for an identical call, records failures without caching them, holds
    irreversible calls for approval, and streams lifecycle events as
    Server-Sent Events in the proxy's wire format.

What it does not do:
    It is a single-process, single-tenant tool with no authentication,
    no row-level security, no advisory locks, and no production
    guarantees. Safe tools bypass it entirely, exactly as they do
    against the production proxy.

Tool tiers and approvals:
    The SDK does not send a tool's tier, so the local server cannot know
    which calls are irreversible. Name the tools that should wait for
    approval with ``--irreversible`` (a repeatable ``fnmatch`` pattern);
    every other intercepted call executes. Granting an approval journals
    a marked mock result, because the tool lives in the agent's process
    and the server has no upstream executor to call.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from undolog_sdk.signature import call_signature

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
"""Loopback only: the local server has no authentication."""

DEFAULT_PORT = 8080
"""The port the SDK's default client uses when ``UNDOLOG_PROXY_URL`` is unset."""

DEFAULT_DB_PATH = "undolog-dev.db"
"""Journal file created in the working directory."""

DEFAULT_ORG_ID = "local"
"""Organisation recorded when a request carries no organisation header."""

_MAX_BODY_BYTES = 1024 * 1024
"""Request body ceiling, matching the proxy's default."""

_MAX_CHUNK_LINE_BYTES = 8 * 1024
"""Longest chunk-size or trailer line read before the body is refused."""

_SSE_QUEUE_SIZE = 128
"""Events buffered per subscriber before the server starts dropping."""

_SSE_HEARTBEAT_SECONDS = 25.0
"""Idle interval before a ``: ping`` comment keeps the stream open."""

_SERVE_POLL_INTERVAL_SECONDS = 0.2
"""How often ``serve_forever`` checks for a shutdown request."""

_MEMORY_DB_PREFIX = "file:undolog-dev-memory-"
"""Shared-cache name used when the journal is held in memory."""

_DEFAULT_APPROVAL_LIMIT = 100
"""Approvals returned by ``GET /approvals`` when no limit is given."""

_MAX_APPROVAL_LIMIT = 500
"""Upper bound on the ``limit`` query parameter."""

_EFFECT_EXECUTING = "executing"
"""Journaled, result not yet reported: the call may run again on a retry."""

_EFFECT_AWAITING_APPROVAL = "awaiting_approval"
"""Held until a human resolves the linked approval request."""

_EFFECT_COMMITTED = "committed"
"""Result journaled: an identical call replays it."""

_EFFECT_FAILED = "failed"
"""Reported as failed. Excluded from the replay cache so a fixed tool can run."""

_EFFECT_REJECTED = "rejected"
"""Held call whose approval was refused. Excluded from the replay cache."""

_LIVE_EFFECT_STATUSES = (
    _EFFECT_EXECUTING,
    _EFFECT_AWAITING_APPROVAL,
    _EFFECT_COMMITTED,
)
"""Statuses that occupy a call signature. Failed and rejected do not."""

_APPROVAL_PENDING = "pending"
_APPROVAL_APPROVED = "approved"
_APPROVAL_REJECTED = "rejected"

_EFFECT_INTERCEPTED = "effect_intercepted"
_EFFECT_COMMITTED_EVENT = "effect_committed"
_EFFECT_REPLAYED = "effect_replayed"
_EFFECT_FAILED_EVENT = "effect_failed"
_APPROVAL_REQUIRED = "approval_required"
_APPROVAL_APPROVED_EVENT = "approval_approved"
_APPROVAL_REJECTED_EVENT = "approval_rejected"

_APPROVAL_ACTION_RE = re.compile(r"^/approvals/([^/]+)/(approve|reject)$")
_EFFECT_ACTION_RE = re.compile(r"^/effects/([^/]+)/(commit|fail)$")


def _utc_now() -> str:
    """Return the current UTC time in RFC 3339 form.

    Returns:
        Timestamp such as ``2026-10-09T08:41:12.512004+00:00``.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _mock_tool_result(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build the result journaled when a human grants an approval.

    The local server cannot call the approved tool, because the tool
    lives in the agent's process and the server has no upstream
    executor. The result is therefore a placeholder that names itself:
    ``mock`` is ``True``, so a replayed approval is never mistaken for
    real tool output.

    Parameters:
        tool_name: Name of the tool that was approved.
        args: Arguments the held call was made with.

    Returns:
        A ``ToolResult`` envelope whose ``output`` is marked as a mock.
    """
    return {
        "success": True,
        "output": {"mock": True, "tool_name": tool_name, "args": args},
        "duration_ms": 0,
    }


class DevJournal:
    """SQLite storage adapter for the local development server.

    The journal holds two tables. ``effects`` records every intercepted
    call, keyed by its call signature, and ``approvals`` records the
    human decisions that irreversible calls wait on.

    A call signature is only occupied by a *live* effect: one that is
    executing, awaiting approval, or committed. Failed and rejected
    effects stay in the table as history but leave the signature free,
    so a fixed tool or a retried call runs again instead of replaying a
    result that never happened.

    Parameters:
        db_path: Filesystem path of the SQLite database. The file and
            its schema are created on first use.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._in_memory = db_path == ":memory:"
        if self._in_memory:
            # Every request opens its own connection, so a private
            # in-memory database would be empty again by the next one.
            # A shared-cache in-memory database outlives the connection
            # that created it only while one stays open, so the journal
            # anchors it for its own lifetime.
            self._dsn: str = (
                f"{_MEMORY_DB_PREFIX}{uuid.uuid4().hex}?mode=memory&cache=shared"
            )
            self._anchor: sqlite3.Connection | None = sqlite3.connect(
                self._dsn, uri=True
            )
            self._anchor.execute("SELECT 1")
        else:
            self._dsn = db_path
            self._anchor = None
        self._create_schema()

    @property
    def db_path(self) -> str:
        """Path of the SQLite file backing this journal."""
        return self._db_path

    def close(self) -> None:
        """Release the connection that anchors an in-memory journal.

        A file-backed journal holds no long-lived connection and needs
        no close, but calling this is always safe.
        """
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    def _connect(self) -> sqlite3.Connection:
        """Open a connection for the calling thread.

        SQLite connections are not shareable between threads, and the
        server handles each request on its own thread, so every
        operation opens and closes its own connection.

        Returns:
            A connection in autocommit mode with a write timeout, so a
            concurrent writer waits instead of failing.
        """
        conn = sqlite3.connect(
            self._dsn,
            timeout=5.0,
            uri=self._in_memory,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self) -> None:
        """Create the tables and indexes, safely re-run on every start.

        Every statement is ``IF NOT EXISTS``, so an existing journal is
        left untouched and the server can be started repeatedly against
        the same file.
        """
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS effects (
                    id          TEXT PRIMARY KEY,
                    org_id      TEXT NOT NULL,
                    session_id  TEXT NOT NULL,
                    tool_name   TEXT NOT NULL,
                    step_index  INTEGER NOT NULL,
                    signature   TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    args        TEXT NOT NULL,
                    result      TEXT,
                    error       TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS effects_live_signature_idx
                    ON effects (signature)
                    WHERE status IN ('executing', 'awaiting_approval',
                                     'committed');

                CREATE INDEX IF NOT EXISTS effects_session_idx
                    ON effects (session_id, step_index);

                CREATE TABLE IF NOT EXISTS approvals (
                    id          TEXT PRIMARY KEY,
                    org_id      TEXT NOT NULL,
                    session_id  TEXT NOT NULL,
                    effect_id   TEXT NOT NULL,
                    tool_name   TEXT NOT NULL,
                    args        TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE INDEX IF NOT EXISTS approvals_status_idx
                    ON approvals (status, created_at);
                """
            )

    def create_effect(
        self,
        *,
        org_id: str,
        session_id: str,
        tool_name: str,
        step_index: int,
        args: dict[str, Any],
        signature: str,
        status: str,
        approval_id: str | None = None,
    ) -> str | None:
        """Journal a new call, optionally with its approval request.

        The effect and its approval are written in one transaction, so a
        call never waits for an approval that was not recorded. The
        unique index on live signatures makes this the exactly-once
        gate: a second call with the same signature is refused rather
        than journaled twice.

        Parameters:
            org_id: Organisation the call belongs to.
            session_id: Session that produced the call.
            tool_name: Logical name of the tool.
            step_index: Call order within the session.
            args: Tool arguments as a JSON-compatible mapping.
            signature: Canonical call signature of the call.
            status: Initial status of the effect.
            approval_id: Identifier of the approval request to create
                alongside the effect, for a call that must wait.

        Returns:
            The new effect identifier, or ``None`` when a live effect
            already holds this signature.
        """
        effect_id = str(uuid.uuid4())
        created_at = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO effects (
                    id, org_id, session_id, tool_name, step_index, signature,
                    status, args, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect_id,
                    org_id,
                    session_id,
                    tool_name,
                    step_index,
                    signature,
                    status,
                    json.dumps(args),
                    created_at,
                    created_at,
                ),
            )
            if cursor.rowcount == 0:
                conn.execute("ROLLBACK")
                return None
            if approval_id is not None:
                conn.execute(
                    """
                    INSERT INTO approvals (
                        id, org_id, session_id, effect_id, tool_name, args,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        org_id,
                        session_id,
                        effect_id,
                        tool_name,
                        json.dumps(args),
                        _APPROVAL_PENDING,
                        created_at,
                    ),
                )
            conn.execute("COMMIT")
        log.debug(
            "effect_created effect_id=%s tool=%s step=%d status=%s",
            effect_id,
            tool_name,
            step_index,
            status,
        )
        return effect_id

    def find_live_effect(self, signature: str) -> dict[str, Any] | None:
        """Return the live effect carrying a call signature, if any.

        Parameters:
            signature: Canonical call signature to look up.

        Returns:
            The effect as a mapping with decoded ``args`` and ``result``,
            or ``None`` when no live effect holds the signature.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM effects
                WHERE signature = ? AND status IN (?, ?, ?)
                """,
                (signature, *_LIVE_EFFECT_STATUSES),
            ).fetchone()
        return self._effect_to_dict(row) if row is not None else None

    def record_commit(self, effect_id: str, result: dict[str, Any]) -> bool:
        """Store a tool result and mark the effect committed.

        Parameters:
            effect_id: Effect the result belongs to.
            result: ``ToolResult`` envelope to journal.

        Returns:
            ``True`` when the effect was found and updated, ``False``
            when it does not exist.
        """
        updated = self._update_effect(
            effect_id,
            status=_EFFECT_COMMITTED,
            result=json.dumps(result),
        )
        if updated:
            log.debug("effect_committed effect_id=%s", effect_id)
        return updated

    def record_fail(self, effect_id: str, error: str) -> bool:
        """Mark an effect failed, freeing its signature for a retry.

        Parameters:
            effect_id: Effect that failed.
            error: Error text reported by the caller.

        Returns:
            ``True`` when the effect was found and updated, ``False``
            when it does not exist.
        """
        updated = self._update_effect(
            effect_id,
            status=_EFFECT_FAILED,
            error=error,
        )
        if updated:
            log.warning("effect_failed effect_id=%s error=%s", effect_id, error)
        return updated

    def mark_rejected(self, effect_id: str) -> bool:
        """Mark a held effect rejected, freeing its signature.

        Parameters:
            effect_id: Effect whose approval was refused.

        Returns:
            ``True`` when the effect was found and updated, ``False``
            when it does not exist.
        """
        return self._update_effect(effect_id, status=_EFFECT_REJECTED)

    def find_pending_approval(self, effect_id: str) -> dict[str, Any] | None:
        """Return the pending approval linked to an effect.

        Parameters:
            effect_id: Effect the approval belongs to.

        Returns:
            The approval as a mapping with decoded ``args``, or ``None``
            when the effect has no unresolved approval.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM approvals
                WHERE effect_id = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (effect_id, _APPROVAL_PENDING),
            ).fetchone()
        return self._approval_to_dict(row) if row is not None else None

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        """Return one approval by identifier.

        Parameters:
            approval_id: Approval identifier.

        Returns:
            The approval as a mapping with decoded ``args``, or ``None``
            when no such approval exists.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        return self._approval_to_dict(row) if row is not None else None

    def resolve_approval(self, approval_id: str, status: str) -> bool:
        """Resolve a pending approval and stamp the resolution time.

        Parameters:
            approval_id: Approval to resolve.
            status: ``approved`` or ``rejected``.

        Returns:
            ``True`` when a pending approval was resolved, ``False`` when
            it was missing or already resolved. The conditional update
            makes a concurrent second decision lose, which is what the
            proxy reports as a conflict.
        """
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE approvals
                SET status = ?, resolved_at = ?
                WHERE id = ? AND status = ?
                """,
                (status, _utc_now(), approval_id, _APPROVAL_PENDING),
            )
        resolved = cursor.rowcount > 0
        if resolved:
            log.info("approval_%s approval_id=%s", status, approval_id)
        return resolved

    def list_approvals(
        self,
        state: str = _APPROVAL_PENDING,
        limit: int = _DEFAULT_APPROVAL_LIMIT,
    ) -> list[dict[str, Any]]:
        """List approvals, newest first.

        Parameters:
            state: ``pending``, ``approved``, ``rejected``, or ``all``
                for every approval regardless of state.
            limit: Maximum approvals to return. A value below one uses
                the default, and the result is never longer than the
                maximum.

        Returns:
            Approval mappings with decoded ``args``, ordered by creation
            time descending.
        """
        if limit < 1:
            limit = _DEFAULT_APPROVAL_LIMIT
        clamped = min(limit, _MAX_APPROVAL_LIMIT)
        with closing(self._connect()) as conn:
            if state == "all":
                rows = conn.execute(
                    """
                    SELECT * FROM approvals
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (clamped,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM approvals
                    WHERE status = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (state, clamped),
                ).fetchall()
        return [self._approval_to_dict(row) for row in rows]

    def _update_effect(
        self,
        effect_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Set the status and reported outcome of one effect.

        Only a held call is ever refused or failed, so it has no result
        of its own to preserve.

        Parameters:
            effect_id: Effect to update.
            status: New status of the effect.
            result: Serialised ``ToolResult`` envelope, for a commit.
            error: Error text, for a failure.

        Returns:
            ``True`` when a row was updated, ``False`` when the effect
            does not exist.
        """
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE effects
                SET status = ?, result = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, result, error, _utc_now(), effect_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _effect_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Convert an ``effects`` row into a mapping.

        Parameters:
            row: Row read from the ``effects`` table.

        Returns:
            The row as a mapping, with ``args`` and ``result`` decoded
            from JSON.
        """
        effect = dict(row)
        effect["args"] = json.loads(effect["args"])
        effect["result"] = (
            json.loads(effect["result"]) if effect["result"] is not None else None
        )
        return effect

    @staticmethod
    def _approval_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Convert an ``approvals`` row into a mapping.

        Parameters:
            row: Row read from the ``approvals`` table.

        Returns:
            The row as a mapping with ``args`` decoded from JSON.
        """
        approval = dict(row)
        approval["args"] = json.loads(approval["args"])
        return approval


class _EventBroadcaster:
    """Fan lifecycle events out to Server-Sent Events subscribers.

    Each subscriber owns a bounded queue, and a full queue drops the
    event rather than blocking the request that produced it, matching
    the proxy's behaviour. Closing the broadcaster releases every
    subscriber so the server can shut down without waiting for a
    heartbeat.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[str | None]] = set()
        self._closed = False

    def subscribe(self) -> queue.Queue[str | None]:
        """Register a new subscriber.

        Returns:
            The queue that will receive formatted event frames. A
            ``None`` frame means the server is shutting down.
        """
        subscription: queue.Queue[str | None] = queue.Queue(_SSE_QUEUE_SIZE)
        with self._lock:
            if self._closed:
                subscription.put(None)
            else:
                self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: queue.Queue[str | None]) -> None:
        """Remove a subscriber.

        Parameters:
            subscription: Queue returned by :meth:`subscribe`.
        """
        with self._lock:
            self._subscribers.discard(subscription)

    def emit(self, frame: str) -> None:
        """Send one formatted event frame to every subscriber.

        Parameters:
            frame: Complete Server-Sent Events frame, including the
                trailing blank line.
        """
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscription in subscribers:
            try:
                subscription.put_nowait(frame)
            except queue.Full:
                log.debug("dropping event for a full subscriber queue")

    def close(self) -> None:
        """Release every subscriber and refuse new ones."""
        with self._lock:
            self._closed = True
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
        for subscription in subscribers:
            subscription.put(None)


def _format_sse_frame(envelope: dict[str, Any]) -> str:
    """Format one event as a Server-Sent Events frame.

    The frame matches the proxy's wire format: an ``event`` line naming
    the event type, an ``id`` line carrying the publish time in
    nanoseconds, and a ``data`` line with the JSON envelope.

    Parameters:
        envelope: Event envelope with at least a ``type`` key.

    Returns:
        A complete frame terminated by a blank line.
    """
    return (
        f"event: {envelope['type']}\n"
        f"id: {time.time_ns()}\n"
        f"data: {json.dumps(envelope)}\n\n"
    )


class _RequestError(Exception):
    """A request the local server refuses, carrying its HTTP response.

    Parameters:
        status: HTTP status code to respond with.
        code: Machine-readable error code, matching the proxy's codes.
        message: Human-readable explanation.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class _DevHttpServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying the journal and its subscribers.

    Each request runs on its own thread, which is what lets a
    Server-Sent Events stream stay open while other requests are
    served.

    Parameters:
        server_address: ``(host, port)`` tuple to bind. Port ``0``
            selects a free port.
        handler_class: Request handler to instantiate per connection.
        journal: Journal the handlers read and write.
        broadcaster: Event fan-out the handlers publish to.
        irreversible_patterns: ``fnmatch`` patterns naming the tools
            that must wait for approval.
    """

    allow_reuse_address = True
    daemon_threads = True
    # Event-stream threads may wait out a heartbeat, so shutdown does not
    # join them. They are daemons, so the interpreter still exits cleanly.
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        journal: DevJournal,
        broadcaster: _EventBroadcaster,
        irreversible_patterns: Sequence[str],
    ) -> None:
        self.journal = journal
        self.broadcaster = broadcaster
        self.irreversible_patterns = tuple(irreversible_patterns)
        super().__init__(server_address, handler_class)

    def handle_error(
        self,
        request: Any,
        client_address: Any,
    ) -> None:
        """Log an unhandled handler exception instead of printing it.

        ``socketserver`` defaults to writing tracebacks to stderr. This
        local server routes them through ``logging`` like the rest of
        the SDK, and stays quiet about client disconnects, which are
        routine on an interrupted event stream.

        Parameters:
            request: Raw request that raised.
            client_address: Address the request came from.
        """
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            log.debug("client %s disconnected", client_address)
            return
        # socketserver's own implementation prints the traceback to
        # stderr, so it is overridden: the SDK routes every message
        # through logging, including an unexpected one.
        log.error("unhandled error serving %s", client_address, exc_info=True)


class _DevHandler(BaseHTTPRequestHandler):
    """HTTP request handler implementing the proxy's contract locally.

    Routes:
        ``GET /health``
            Liveness probe.
        ``GET /events``
            Server-Sent Events stream of lifecycle events.
        ``GET /approvals``
            Approval list, filtered by ``state`` and ``limit``.
        ``POST /mcp/tool_call``
            Intercept one call and decide whether to execute it, replay
            a committed result, or hold it for approval.
        ``POST /approvals/{id}/approve`` and ``.../reject``
            Resolve a held call.
        ``PUT /effects/{id}/commit`` and ``.../fail``
            Report the outcome of a call the server let run.
    """

    # The proxy answers HTTP/1.1, so kept-alive connections and the
    # framing rules that come with them are part of the contract this
    # server mirrors.
    protocol_version = "HTTP/1.1"
    server_version = "undolog-dev"
    sys_version = ""
    server: _DevHttpServer
    _undolog_request_id: str | None = None
    # Set once a request body has been read in full. A response sent
    # while unread bytes remain has to close the connection, because
    # those bytes would otherwise be parsed as the next request.
    _body_read: bool = False

    def do_GET(self) -> None:
        """Handle every GET route."""
        self._dispatch()

    def do_POST(self) -> None:
        """Handle every POST route."""
        self._dispatch()

    def do_PUT(self) -> None:
        """Handle every PUT route."""
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:
        """Route request logging through the SDK's logger.

        Parameters:
            format: printf-style message supplied by the base class.
            *args: Values interpolated into the message.
        """
        log.debug("%s %s", self.client_address[0], format % args)

    def _dispatch(self) -> None:
        """Route the request to its handler, or refuse it."""
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/health":
                self._require_method("GET")
                self._handle_health()
            elif path == "/events":
                self._require_method("GET")
                self._handle_events()
            elif path == "/approvals":
                self._require_method("GET")
                self._handle_list_approvals(parsed.query)
            elif path == "/mcp/tool_call":
                self._require_method("POST")
                self._handle_tool_call()
            else:
                approval = _APPROVAL_ACTION_RE.match(path)
                effect = _EFFECT_ACTION_RE.match(path)
                if approval is not None:
                    self._require_method("POST")
                    self._handle_approval_decision(approval.group(1), approval.group(2))
                elif effect is not None:
                    self._require_method("PUT")
                    self._handle_effect_action(effect.group(1), effect.group(2))
                else:
                    self._send_error(404, "not_found", "unknown path")
        except _RequestError as exc:
            self._send_error(exc.status, exc.code, exc.message)
        except ValueError as exc:
            self._send_error(400, "invalid_request", str(exc))

    def _require_method(self, allowed: str) -> None:
        """Refuse a request that uses the wrong verb for a known path.

        Parameters:
            allowed: The single HTTP method the route accepts.

        Raises:
            _RequestError: When the request used another method.
        """
        if self.command != allowed:
            raise _RequestError(
                405,
                "method_not_allowed",
                f"{allowed} required",
            )

    def _org_id(self) -> str:
        """Read the organisation a request belongs to.

        The proxy derives the organisation from an API key. The local
        server has no keys, so it takes the header the SDK already
        sends and falls back to a fixed local organisation, which keeps
        an unconfigured request usable.

        Returns:
            Organisation identifier, or ``local`` when none was sent.
        """
        return (
            self.headers.get("X-UndoLog-Org-Id")
            or self.headers.get("X-Org-Id")
            or DEFAULT_ORG_ID
        )

    def _read_body(self) -> dict[str, Any]:
        """Read and decode a JSON request body.

        The body is consumed before any response is written, because a
        response sent while the client is still sending is discarded
        when the connection resets.

        Returns:
            The decoded body, or an empty mapping when the request
            carried no body.

        Raises:
            _RequestError: When the body exceeds the size ceiling or is
                not valid JSON.
        """
        raw = b""
        overflow = False
        if self.headers.get("Transfer-Encoding") is not None:
            # A chunked body carries no length, so it is read a chunk
            # at a time and anything past the ceiling is discarded
            # rather than kept.
            raw, overflow = self._read_chunked()
            self._body_read = True
        else:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise _RequestError(
                    400,
                    "invalid_request",
                    "invalid Content-Length header",
                ) from exc
            if length == 0:
                self._body_read = True
            elif length <= _MAX_BODY_BYTES:
                raw = self.rfile.read(length)
                self._body_read = True
            else:
                # Left unread on purpose: the response that refuses it
                # drains the body on its way out.
                overflow = True
        if overflow:
            raise _RequestError(
                413,
                "body_too_large",
                "request body exceeds the configured limit",
            )
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _RequestError(
                400,
                "invalid_request",
                "invalid JSON body",
            ) from exc
        if not isinstance(decoded, dict):
            raise _RequestError(
                400,
                "invalid_request",
                "request body must be a JSON object",
            )
        return cast(dict[str, Any], decoded)

    def _read_chunked(self) -> tuple[bytes, bool]:
        """Read a body framed with the chunked transfer coding.

        Bytes past the size ceiling are read and thrown away instead
        of kept, so memory stays bounded while the stream still
        reaches its terminator and the connection stays usable.

        Returns:
            The reassembled body, and whether it passed the size
            ceiling.
        """
        parts: list[bytes] = []
        total = 0
        overflow = False
        while True:
            size_line = self.rfile.readline(_MAX_CHUNK_LINE_BYTES)
            if not size_line:
                raise _RequestError(400, "invalid_request", "truncated request body")
            try:
                size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise _RequestError(
                    400,
                    "invalid_request",
                    "invalid chunk size",
                ) from exc
            if size == 0:
                # Trailer fields follow the last chunk and end at an
                # empty line. They carry nothing this server uses, so
                # they are read only to leave the stream in step.
                while True:
                    trailer = self.rfile.readline(_MAX_CHUNK_LINE_BYTES)
                    if not trailer or trailer in (b"\r\n", b"\n"):
                        break
                return b"".join(parts), overflow
            if overflow or total + size > _MAX_BODY_BYTES:
                overflow = True
                self._consume(size)
            else:
                data = self.rfile.read(size)
                if len(data) < size:
                    raise _RequestError(
                        400,
                        "invalid_request",
                        "truncated request body",
                    )
                parts.append(data)
                total += size
            # Chunk data is followed by a carriage return and newline.
            self._consume(2)

    def _consume(self, length: int) -> None:
        """Read and discard request bytes.

        Parameters:
            length: Number of bytes to discard.

        Raises:
            _RequestError: When the body ends before the bytes do.
        """
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                raise _RequestError(400, "invalid_request", "truncated request body")
            remaining -= len(chunk)

    def _discard_unread_body(self) -> None:
        """Consume a request body that no handler read.

        A response written while the client is still sending is lost
        when the connection closes, because pending received data
        resets it. Reading the body first keeps the response intact and
        leaves the connection in step for the next request.
        """
        if self._body_read:
            return
        if self.headers.get("Transfer-Encoding") is not None:
            # Nothing announced this body's end, so it cannot be
            # consumed here. The connection closes after the response.
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            # The length was never understood, so where the stream ends
            # is unknown and the connection cannot be reused.
            return
        if length == 0:
            self._body_read = True
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        self._body_read = True

    def _body_is_unread(self) -> bool:
        """Report whether a request body arrived but was never read.

        Returns:
            ``True`` when unread body bytes remain on the connection.
            Answering without consuming them would leave the connection
            reading a body as though it were the next request, so a
            response sent in that state closes the connection instead.
        """
        if self._body_read:
            return False
        if self.headers.get("Transfer-Encoding") is not None:
            return True
        try:
            return int(self.headers.get("Content-Length", "0")) > 0
        except ValueError:
            # The length was never understood, so where the stream ends
            # is unknown and the connection cannot be reused.
            return True

    def _send_json(self, status: int, payload: Any) -> None:
        """Respond with a JSON body.

        Parameters:
            status: HTTP status code.
            payload: JSON-serialisable response body. ``GET /approvals``
                answers with a bare array, like the proxy does.
        """
        # The body is consumed before the response is written, so a
        # refusal reaches a client that is still sending.
        self._discard_unread_body()
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", self._request_id())
        if self._body_is_unread():
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str) -> None:
        """Respond with the proxy's error envelope.

        Parameters:
            status: HTTP status code.
            code: Machine-readable error code.
            message: Human-readable explanation.
        """
        log.debug(
            "request_rejected status=%d code=%s message=%s", status, code, message
        )
        self._send_json(
            status,
            {
                "request_id": self._request_id(),
                "code": code,
                "message": message,
                "timestamp": _utc_now(),
            },
        )

    def _request_id(self) -> str:
        """Return the identifier assigned to this request.

        The proxy mints one identifier per request, overwriting any
        inbound ``X-Request-Id``, and sends it back on the response.
        A local request answers the same way: the first call
        generates a UUID and later calls in the same request return
        it.

        Returns:
            The identifier generated for the request.
        """
        if self._undolog_request_id is None:
            self._undolog_request_id = str(uuid.uuid4())
        return self._undolog_request_id

    def _emit(
        self,
        event_type: str,
        *,
        org_id: str,
        session_id: str | None = None,
        effect_id: str | None = None,
        approval_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish one lifecycle event to the event stream.

        Parameters:
            event_type: Event name, matching the proxy's vocabulary.
            org_id: Organisation the event belongs to.
            session_id: Session the event belongs to, when it has one.
            effect_id: Effect the event concerns, when it has one.
            approval_id: Approval the event concerns, when it has one.
            payload: Extra event body.
        """
        envelope: dict[str, Any] = {
            "type": event_type,
            "timestamp": _utc_now(),
            "org_id": org_id,
        }
        if session_id is not None:
            envelope["session_id"] = session_id
        if effect_id is not None:
            envelope["effect_id"] = effect_id
        if approval_id is not None:
            envelope["approval_id"] = approval_id
        if payload is not None:
            envelope["payload"] = payload
        self.server.broadcaster.emit(_format_sse_frame(envelope))

    def _handle_health(self) -> None:
        """Report that the local server is listening."""
        self._send_json(200, {"status": "ok", "service": "undolog-dev"})

    def _handle_events(self) -> None:
        """Stream lifecycle events until the client disconnects."""
        subscription = self.server.broadcaster.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Request-Id", self._request_id())
            self.end_headers()
            self.close_connection = True
            while True:
                try:
                    frame = subscription.get(timeout=_SSE_HEARTBEAT_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if frame is None:
                    break
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            log.debug("event subscriber disconnected")
        finally:
            self.server.broadcaster.unsubscribe(subscription)

    def _handle_tool_call(self) -> None:
        """Intercept one call and decide what the caller should do.

        The decision follows the engine's rules, in order: a signature
        that is already committed replays its result, a signature that
        is executing is allowed to run again, a signature awaiting
        approval reports the same pending approval, and a new signature
        either runs or waits, depending on whether the tool was named
        irreversible.
        """
        body = self._read_body()
        session_id = body.get("session_id")
        tool_name = body.get("tool_name")
        if not isinstance(session_id, str) or not session_id:
            raise _RequestError(
                400,
                "invalid_request",
                "session_id and tool_name are required",
            )
        if not isinstance(tool_name, str) or not tool_name:
            raise _RequestError(
                400,
                "invalid_request",
                "session_id and tool_name are required",
            )
        args = body.get("args", {})
        if not isinstance(args, dict):
            raise _RequestError(400, "invalid_request", "args must be a JSON object")
        step_index = body.get("step_index", 0)
        if not isinstance(step_index, int) or isinstance(step_index, bool):
            raise _RequestError(
                400,
                "invalid_request",
                "step_index must be a non-negative integer",
            )
        if step_index < 0:
            raise _RequestError(
                400,
                "invalid_request",
                "step_index must be a non-negative integer",
            )
        org_id = self._org_id()
        try:
            signature = call_signature(session_id, step_index, tool_name, args)
        except ValueError as exc:
            raise _RequestError(
                400,
                "invalid_request",
                f"unable to canonicalize tool args: {exc}",
            ) from exc

        self._emit(
            _EFFECT_INTERCEPTED,
            org_id=org_id,
            session_id=session_id,
            payload={"stage": "intercepted"},
        )

        existing = self.server.journal.find_live_effect(signature)
        effect_id: str | None = None
        approval_id: str | None = None
        if existing is None:
            status = _EFFECT_EXECUTING
            if self._requires_approval(tool_name):
                status = _EFFECT_AWAITING_APPROVAL
                approval_id = str(uuid.uuid4())
            effect_id = self.server.journal.create_effect(
                org_id=org_id,
                session_id=session_id,
                tool_name=tool_name,
                step_index=step_index,
                args=args,
                signature=signature,
                status=status,
                approval_id=approval_id,
            )
            if effect_id is None:
                # A concurrent call claimed the signature first, so this
                # one answers from the row that call journaled.
                existing = self.server.journal.find_live_effect(signature)

        if existing is not None:
            self._respond_for_live_effect(existing, org_id)
            return

        if approval_id is not None:
            self._emit(
                _APPROVAL_REQUIRED,
                org_id=org_id,
                session_id=session_id,
                effect_id=effect_id,
                approval_id=approval_id,
                payload={"stage": "approval_required"},
            )
            self._send_json(
                202,
                {
                    "status": "pending_approval",
                    "approval_id": approval_id,
                    "retry_after": 5,
                },
            )
            return

        log.info(
            "intercept_executed tool=%s step=%d session=%s",
            tool_name,
            step_index,
            session_id,
        )
        self._send_json(
            200,
            {"status": "executed", "effect_id": effect_id, "result": None},
        )

    def _respond_for_live_effect(self, effect: dict[str, Any], org_id: str) -> None:
        """Answer an intercept for a call the journal already holds.

        Parameters:
            effect: Existing live effect carrying the call signature.
            org_id: Organisation the call belongs to.
        """
        status = effect["status"]
        if status == _EFFECT_COMMITTED:
            self._emit(
                _EFFECT_REPLAYED,
                org_id=org_id,
                session_id=effect["session_id"],
                effect_id=effect["id"],
                payload={"stage": "replayed"},
            )
            self._send_json(
                200,
                {
                    "status": "replayed",
                    "effect_id": effect["id"],
                    "result": effect["result"],
                },
            )
            return
        if status == _EFFECT_AWAITING_APPROVAL:
            approval = self.server.journal.find_pending_approval(effect["id"])
            if approval is not None:
                self._send_json(
                    202,
                    {
                        "status": "pending_approval",
                        "approval_id": approval["id"],
                        "retry_after": 5,
                    },
                )
                return
            # The approval was resolved between the lookup and the read,
            # so the call is free to run.
        self._send_json(
            200,
            {"status": "executed", "effect_id": effect["id"], "result": None},
        )

    def _requires_approval(self, tool_name: str) -> bool:
        """Decide whether a tool must wait for a human.

        Parameters:
            tool_name: Logical name of the tool being called.

        Returns:
            ``True`` when the tool matches one of the configured
            irreversible patterns.
        """
        return any(
            fnmatch.fnmatchcase(tool_name, pattern)
            for pattern in self.server.irreversible_patterns
        )

    def _handle_effect_action(self, effect_id: str, action: str) -> None:
        """Record the outcome of a call the server let run.

        Parameters:
            effect_id: Effect the outcome belongs to.
            action: ``commit`` to journal a result, ``fail`` to record a
                failure and free the signature.
        """
        org_id = self._org_id()
        body = self._read_body()
        if action == "commit":
            result = body.get("result", {})
            if not isinstance(result, dict):
                raise _RequestError(
                    400,
                    "invalid_request",
                    "result must be a JSON object",
                )
            if not self.server.journal.record_commit(effect_id, result):
                self._send_error(404, "not_found", "effect not found")
                return
            self._emit(
                _EFFECT_COMMITTED_EVENT,
                org_id=org_id,
                session_id=body.get("session_id"),
                effect_id=effect_id,
                payload={"stage": "committed"},
            )
            self._send_json(200, {"status": "committed", "effect_id": effect_id})
            return
        error = body.get("error", "")
        if not isinstance(error, str):
            raise _RequestError(400, "invalid_request", "error must be a string")
        if not self.server.journal.record_fail(effect_id, error):
            self._send_error(404, "not_found", "effect not found")
            return
        self._emit(
            _EFFECT_FAILED_EVENT,
            org_id=org_id,
            session_id=body.get("session_id"),
            effect_id=effect_id,
            payload={"stage": "execute", "error": "failed"},
        )
        self._send_json(200, {"status": "failed", "effect_id": effect_id})

    def _handle_approval_decision(self, approval_id: str, action: str) -> None:
        """Resolve a held call.

        Granting an approval journals a marked mock result, because the
        approved tool lives in the agent's process and the server has no
        upstream executor. Refusing one frees the signature, so the next
        identical call asks again rather than silently running.

        Approvals are resolved by identifier alone. The local server is
        single-tenant, so it does not check the organisation a decision
        arrives with, and an approval recorded under one organisation id
        can be resolved under another.

        Parameters:
            approval_id: Approval to resolve.
            action: ``approve`` or ``reject``.
        """
        org_id = self._org_id()
        approval = self.server.journal.get_approval(approval_id)
        if approval is None:
            self._send_error(404, "not_found", "approval not found")
            return
        if approval["status"] != _APPROVAL_PENDING:
            self._send_error(409, "conflict", "approval already resolved")
            return
        # The decision body names an optional actor, which this local
        # journal has nowhere to record. It is read so the request body
        # is consumed and the connection stays aligned with the client.
        self._read_body()
        if action == "reject":
            if not self.server.journal.resolve_approval(
                approval_id, _APPROVAL_REJECTED
            ):
                self._send_error(409, "conflict", "approval already resolved")
                return
            self.server.journal.mark_rejected(approval["effect_id"])
            self._emit(
                _APPROVAL_REJECTED_EVENT,
                org_id=org_id,
                session_id=approval["session_id"],
                approval_id=approval_id,
                payload=self._approval_payload(approval_id),
            )
            self._send_json(
                200,
                {"status": "rejected", "approval_id": approval_id},
            )
            return
        if not self.server.journal.resolve_approval(approval_id, _APPROVAL_APPROVED):
            self._send_error(409, "conflict", "approval already resolved")
            return
        result = _mock_tool_result(approval["tool_name"], approval["args"])
        self.server.journal.record_commit(approval["effect_id"], result)
        self._emit(
            _APPROVAL_APPROVED_EVENT,
            org_id=org_id,
            session_id=approval["session_id"],
            approval_id=approval_id,
            effect_id=approval["effect_id"],
            payload=self._approval_payload(approval_id),
        )
        self._send_json(
            200,
            {
                "status": "approved",
                "approval_id": approval_id,
                "effect_id": approval["effect_id"],
                "execution": "committed",
                "result": result,
            },
        )

    def _approval_payload(self, approval_id: str) -> dict[str, Any]:
        """Read an approval for use as an event payload.

        Parameters:
            approval_id: Approval to read.

        Returns:
            The resolved approval, or an empty mapping when it is gone.
        """
        approval = self.server.journal.get_approval(approval_id)
        return approval if approval is not None else {}

    def _handle_list_approvals(self, query: str) -> None:
        """List approvals, newest first.

        Parameters:
            query: Raw query string, carrying ``state`` and ``limit``.
        """
        params = parse_qs(query)
        state = params.get("state", [_APPROVAL_PENDING])[0]
        if state not in (
            _APPROVAL_PENDING,
            _APPROVAL_APPROVED,
            _APPROVAL_REJECTED,
            "all",
        ):
            raise _RequestError(400, "invalid_request", "invalid state")
        limit_raw = params.get("limit", [str(_DEFAULT_APPROVAL_LIMIT)])[0]
        try:
            limit = int(limit_raw)
        except ValueError as exc:
            raise _RequestError(
                400,
                "invalid_request",
                "invalid limit",
            ) from exc
        # A non-positive limit is the journal's business: it falls back
        # to the default, exactly as the proxy does.
        self._send_json(
            200,
            self.server.journal.list_approvals(state=state, limit=limit),
        )


class DevServer:
    """Local UndoLog server backed by SQLite.

    The server is a background thread once started, and it binds on
    construction, so a port that is already taken fails immediately
    rather than on first request::

        server = DevServer(db_path=":memory:")
        server.start()
        ...
        server.stop()

    Parameters:
        host: Interface to bind. Defaults to loopback, because the
            local server has no authentication.
        port: Port to bind. ``0`` selects a free port.
        db_path: Path of the SQLite journal to open or create.
        irreversible_patterns: ``fnmatch`` patterns naming the tools
            that must wait for approval. Every other intercepted call
            executes.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        db_path: str = DEFAULT_DB_PATH,
        irreversible_patterns: Sequence[str] = (),
    ) -> None:
        self._journal = DevJournal(db_path)
        self._broadcaster = _EventBroadcaster()
        self._http = _DevHttpServer(
            (host, port),
            _DevHandler,
            journal=self._journal,
            broadcaster=self._broadcaster,
            irreversible_patterns=irreversible_patterns,
        )
        bound_host, bound_port = cast(tuple[str, int], self._http.server_address)
        self._url = f"http://{bound_host}:{bound_port}"
        self._thread: threading.Thread | None = None
        self._serving = False

    @property
    def url(self) -> str:
        """Base URL the server answers on."""
        return self._url

    @property
    def db_path(self) -> str:
        """Path of the SQLite journal backing this server."""
        return self._journal.db_path

    def start(self) -> None:
        """Serve on a background thread.

        The server is ready to accept connections when this returns.
        """
        self._serving = True
        self._thread = threading.Thread(
            target=self._serve,
            name="undolog-dev",
            daemon=True,
        )
        self._thread.start()

    def serve_forever(self) -> None:
        """Serve on the calling thread until interrupted."""
        self._serving = True
        try:
            self._http.serve_forever(poll_interval=_SERVE_POLL_INTERVAL_SECONDS)
        finally:
            self._serving = False

    def stop(self) -> None:
        """Stop serving and release every open connection."""
        self._broadcaster.close()
        if self._serving:
            self._http.shutdown()
            self._serving = False
        self._http.server_close()
        self._journal.close()
        if self._thread is not None:
            self._thread.join(timeout=_SERVE_POLL_INTERVAL_SECONDS * 10)
            self._thread = None
        log.debug("undolog dev stopped")

    def _serve(self) -> None:
        """Serve on the background thread started by :meth:`start`."""
        try:
            self._http.serve_forever(poll_interval=_SERVE_POLL_INTERVAL_SECONDS)
        finally:
            self._serving = False


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for ``undolog``.

    Returns:
        A parser whose ``dev`` subcommand starts the local server.
    """
    parser = argparse.ArgumentParser(
        prog="undolog",
        description="UndoLog command-line tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dev = subparsers.add_parser(
        "dev",
        help="run a local UndoLog server backed by SQLite",
        description=(
            "Start a local UndoLog server on http://127.0.0.1:8080 with no "
            "configuration, so decorated tools reach it as soon as it is "
            "running."
        ),
        epilog=(
            "Limitations: the local server is single-process and "
            "single-tenant, has no authentication, no row-level security, "
            "and no advisory locks, and is not suitable for production. "
            "Safe tools bypass it entirely. The SDK does not send tool "
            "tiers, so name the tools that must wait for approval with "
            "--irreversible. Granting an approval journals a marked mock "
            "result, because the server cannot call a tool that lives in "
            "your process."
        ),
    )
    dev.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"interface to bind (default: {DEFAULT_HOST})",
    )
    dev.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to bind (default: {DEFAULT_PORT})",
    )
    dev.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"SQLite journal file (default: {DEFAULT_DB_PATH})",
    )
    dev.add_argument(
        "--irreversible",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "fnmatch pattern naming a tool that must wait for approval; "
            "repeat for more than one tool"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``undolog`` command-line interface.

    Parameters:
        argv: Arguments to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` when the server shut down cleanly, ``1`` when it could
        not bind or could not open its journal.
    """
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        server = DevServer(
            host=args.host,
            port=args.port,
            db_path=args.db,
            irreversible_patterns=tuple(args.irreversible),
        )
    except sqlite3.Error as exc:
        log.error("cannot open the journal at %s: %s", args.db, exc)
        return 1
    except OSError as exc:
        log.error("cannot listen on %s:%s: %s", args.host, args.port, exc)
        return 1
    log.info(
        "undolog dev listening on %s (journal: %s)",
        server.url,
        server.db_path,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("interrupted, shutting down")
    finally:
        server.stop()
    return 0
