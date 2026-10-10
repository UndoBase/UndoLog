"""Tests for the local development server behind ``undolog dev``.

Every test that speaks HTTP runs against a server on a real socket,
because the value of the local server is that it talks the same
protocol as the proxy. The suite covers the SQLite storage adapter,
the HTTP contract the SDK's client depends on, the Server-Sent Events
stream, the full decorator lifecycle, and the command-line entry
point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import socket
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from undolog_sdk import dev as dev_mod
from undolog_sdk.client import UndoLogClient, _default_proxy_url
from undolog_sdk.context import run_with_session
from undolog_sdk.decorators import AwaitingApprovalError, undolog_tool
from undolog_sdk.dev import (
    _MAX_BODY_BYTES,
    DEFAULT_DB_PATH,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DevJournal,
    DevServer,
    _DevHandler,
    _DevHttpServer,
    _EventBroadcaster,
    build_parser,
    main,
)
from undolog_sdk.session import UndoLogSession
from undolog_sdk.tier import CompensationDescriptor, ToolTier

ORG = "org_test"
SESSION_ID = "11111111-1111-1111-1111-111111111111"
_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
"""Shape of the generated identifier that replaces an unsafe one."""


def _tool_call_body(
    *,
    tool_name: str = "send_email",
    step_index: int = 1,
    args: Any = None,
    session_id: str = SESSION_ID,
) -> dict[str, Any]:
    """Build the request body the SDK sends when intercepting a call.

    Parameters:
        tool_name: Logical name of the tool.
        step_index: Call order within the session.
        args: Tool arguments. Typed loosely because one test deliberately
            sends a value that is not an object, to check the server
            refuses it.
        session_id: Session that produced the call.

    Returns:
        A body matching ``UndoLogClient.intercept``.
    """
    return {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_version": "1.0.0",
        "step_index": step_index,
        "args": args if args is not None else {"to": "alice@example.com"},
    }


@asynccontextmanager
async def _http(server: DevServer) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client pointed at a running local server.

    Parameters:
        server: Server to talk to.

    Yields:
        A client using the server's own base URL.
    """
    async with httpx.AsyncClient(base_url=server.url, timeout=5.0) as client:
        yield client


@asynccontextmanager
async def _sdk(server: DevServer) -> AsyncIterator[UndoLogClient]:
    """Yield an SDK client pointed at a running local server.

    Parameters:
        server: Server to talk to.

    Yields:
        A client built the way a user's program builds one, from a URL.
    """
    client = UndoLogClient(proxy_url=server.url)
    try:
        yield client
    finally:
        await client.aclose()


@asynccontextmanager
async def _event_stream(
    client: httpx.AsyncClient,
    expected: int,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Collect Server-Sent Events while the body of the block runs.

    The stream is opened before the block runs, so events emitted inside
    it are not missed.

    Parameters:
        client: HTTP client to stream with.
        expected: Number of events to collect before finishing.

    Yields:
        A list that fills with decoded event envelopes.
    """
    sink: list[dict[str, Any]] = []
    connected = asyncio.Event()

    async def _reader() -> None:
        async with client.stream("GET", "/events") as response:
            assert response.status_code == 200
            connected.set()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    sink.append(json.loads(line[len("data: ") :]))
                    if len(sink) >= expected:
                        return

    task = asyncio.create_task(_reader())
    try:
        await asyncio.wait_for(connected.wait(), timeout=5.0)
        yield sink
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _occupied_port() -> tuple[socket.socket, int]:
    """Reserve a port so a later bind to it fails.

    Returns:
        The holding socket and the port it occupies.
    """
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind((DEFAULT_HOST, 0))
    holder.listen(1)
    return holder, holder.getsockname()[1]


async def _chunked(*parts: bytes) -> AsyncIterator[bytes]:
    """Yield body pieces, which httpx sends without a content length.

    Parameters:
        parts: Body pieces in send order.

    Yields:
        Each piece in turn.
    """
    for part in parts:
        yield part


def _raw_request(server: DevServer, request: bytes, *, hang_up: bool = False) -> str:
    """Send a hand-built request and read the answer to its end.

    Parameters:
        server: Running server to talk to.
        request: One complete request, headers and body included.
        hang_up: Close the sending side once the request is out, as a
            client that stops mid-body does.

    Returns:
        The response as received, status line and body included.
    """
    port = urlparse(server.url).port
    with socket.create_connection((DEFAULT_HOST, port), timeout=5) as sock:
        sock.sendall(request)
        if hang_up:
            sock.shutdown(socket.SHUT_WR)
        received = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
    return received.decode()


def _read_one_response(sock: socket.socket) -> str:
    """Read one response from a socket that stays open afterwards.

    ``_raw_request`` reads to end of stream, which only finishes when
    the server announces ``Connection: close``. A response that keeps
    the connection going is framed by its content length instead, so
    the next request can be sent on the same socket.

    Parameters:
        sock: Connected socket to read from.

    Returns:
        The response head as received, status line and headers
        included, with any body consumed.
    """
    received = b""
    while b"\r\n\r\n" not in received:
        chunk = sock.recv(4096)
        if not chunk:
            break
        received += chunk
    head, separator, rest = received.partition(b"\r\n\r\n")
    if not separator:
        return received.decode()
    length = 0
    for line in head.decode().splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    while len(rest) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        rest += chunk
    return head.decode()


@pytest.fixture
def bare_http(tmp_path: Path) -> Iterator[_DevHttpServer]:
    """Yield a bound HTTP server with nothing serving it.

    Request handlers are exercised against a running server, but the
    failure path they hand to the server itself only runs when a
    handler raises, which no route is meant to do. This yields the
    server object so that path can be driven directly.

    Parameters:
        tmp_path: Directory the journal file is created in.

    Yields:
        A bound server, closed afterwards.
    """
    storage = DevJournal(str(tmp_path / "bare.db"))
    http = _DevHttpServer(
        (DEFAULT_HOST, 0),
        _DevHandler,
        journal=storage,
        broadcaster=_EventBroadcaster(),
        irreversible_patterns=(),
    )
    try:
        yield http
    finally:
        http.server_close()
        storage.close()


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[DevJournal]:
    """Yield a file-backed journal and close it afterwards.

    Parameters:
        tmp_path: Directory the journal file is created in.

    Yields:
        A journal backed by a temporary file.
    """
    storage = DevJournal(str(tmp_path / "journal.db"))
    try:
        yield storage
    finally:
        storage.close()


@pytest.fixture
async def server(tmp_path: Path) -> AsyncIterator[DevServer]:
    """Yield a running local server and stop it afterwards.

    Parameters:
        tmp_path: Directory the journal file is created in.

    Yields:
        A started server bound to a free port.
    """
    running = DevServer(port=0, db_path=str(tmp_path / "journal.db"))
    running.start()
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture
async def gated_server(tmp_path: Path) -> AsyncIterator[DevServer]:
    """Yield a running server that gates one tool behind approval.

    Parameters:
        tmp_path: Directory the journal file is created in.

    Yields:
        A started server whose ``delete_*`` tools wait for a human.
    """
    running = DevServer(
        port=0,
        db_path=str(tmp_path / "gated.db"),
        irreversible_patterns=("delete_*",),
    )
    running.start()
    try:
        yield running
    finally:
        running.stop()


# ── SQLite storage adapter ─────────────────────────────────────────────────


class TestDevJournal:
    """Verify the journal that stands in for the engine's database."""

    def test_create_effect_journals_the_call(self, journal: DevJournal) -> None:
        effect_id = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="send_email",
            step_index=1,
            args={"to": "alice@example.com"},
            signature="sig-1",
            status="executing",
        )

        assert effect_id is not None
        stored = journal.find_live_effect("sig-1")
        assert stored is not None
        assert stored["id"] == effect_id
        assert stored["org_id"] == ORG
        assert stored["session_id"] == SESSION_ID
        assert stored["step_index"] == 1
        assert stored["args"] == {"to": "alice@example.com"}
        assert stored["status"] == "executing"
        assert stored["result"] is None

    def test_create_effect_refuses_a_second_live_signature(
        self,
        journal: DevJournal,
    ) -> None:
        first = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="send_email",
            step_index=1,
            args={},
            signature="sig-1",
            status="executing",
        )
        second = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="send_email",
            step_index=1,
            args={},
            signature="sig-1",
            status="executing",
        )

        assert first is not None
        assert second is None

    def test_a_different_step_index_is_a_different_signature(
        self,
        journal: DevJournal,
    ) -> None:
        first = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="send_email",
            step_index=1,
            args={},
            signature="sig-1",
            status="executing",
        )
        second = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="send_email",
            step_index=2,
            args={},
            signature="sig-2",
            status="executing",
        )

        assert first is not None
        assert second is not None
        assert first != second

    def test_record_commit_makes_the_call_replayable(
        self,
        journal: DevJournal,
    ) -> None:
        effect_id = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="send_email",
            step_index=1,
            args={},
            signature="sig-1",
            status="executing",
        )
        assert effect_id is not None

        assert journal.record_commit(effect_id, {"success": True, "output": "sent"})

        stored = journal.find_live_effect("sig-1")
        assert stored is not None
        assert stored["status"] == "committed"
        assert stored["result"] == {"success": True, "output": "sent"}

    def test_record_commit_reports_an_unknown_effect(self, journal: DevJournal) -> None:
        assert not journal.record_commit("missing-effect", {"success": True})

    def test_failed_effect_is_kept_but_frees_its_signature(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "failures.db"
        storage = DevJournal(str(path))
        try:
            effect_id = storage.create_effect(
                org_id=ORG,
                session_id=SESSION_ID,
                tool_name="send_email",
                step_index=1,
                args={},
                signature="sig-1",
                status="executing",
            )
            assert effect_id is not None
            assert storage.record_fail(effect_id, "smtp unavailable")

            assert storage.find_live_effect("sig-1") is None
            again = storage.create_effect(
                org_id=ORG,
                session_id=SESSION_ID,
                tool_name="send_email",
                step_index=1,
                args={},
                signature="sig-1",
                status="executing",
            )
            assert again is not None
            assert again != effect_id
        finally:
            storage.close()

        # The failure stays on disk as history, and the journal
        # reopens cleanly on the same file.
        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                "SELECT status, error FROM effects WHERE id = ?",
                (effect_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "failed"
        assert row[1] == "smtp unavailable"

    def test_rejected_effect_frees_its_signature(self, journal: DevJournal) -> None:
        effect_id = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="delete_database",
            step_index=1,
            args={},
            signature="sig-1",
            status="awaiting_approval",
            approval_id="approval-1",
        )
        assert effect_id is not None

        assert journal.mark_rejected(effect_id)
        assert journal.find_live_effect("sig-1") is None

    def test_create_effect_writes_the_approval_with_the_effect(
        self,
        journal: DevJournal,
    ) -> None:
        effect_id = journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="delete_database",
            step_index=1,
            args={"db": "prod"},
            signature="sig-1",
            status="awaiting_approval",
            approval_id="approval-1",
        )

        assert effect_id is not None
        approval = journal.get_approval("approval-1")
        assert approval is not None
        assert approval["status"] == "pending"
        assert approval["effect_id"] == effect_id
        assert approval["tool_name"] == "delete_database"
        assert approval["args"] == {"db": "prod"}
        assert approval["resolved_at"] is None
        assert journal.find_pending_approval(effect_id) == approval

    def test_resolve_approval_resolves_exactly_once(
        self,
        journal: DevJournal,
    ) -> None:
        journal.create_effect(
            org_id=ORG,
            session_id=SESSION_ID,
            tool_name="delete_database",
            step_index=1,
            args={},
            signature="sig-1",
            status="awaiting_approval",
            approval_id="approval-1",
        )

        assert journal.resolve_approval("approval-1", "approved")
        assert not journal.resolve_approval("approval-1", "rejected")
        resolved = journal.get_approval("approval-1")
        assert resolved is not None
        assert resolved["status"] == "approved"
        assert resolved["resolved_at"] is not None
        assert journal.find_pending_approval("missing-effect") is None

    def test_list_approvals_orders_newest_first(
        self,
        journal: DevJournal,
    ) -> None:
        for index in range(3):
            journal.create_effect(
                org_id=ORG,
                session_id=SESSION_ID,
                tool_name="delete_database",
                step_index=index + 1,
                args={},
                signature=f"sig-{index}",
                status="awaiting_approval",
                approval_id=f"approval-{index}",
            )

        approvals = journal.list_approvals(state="pending", limit=10)

        assert [approval["id"] for approval in approvals] == [
            "approval-2",
            "approval-1",
            "approval-0",
        ]

    def test_list_approvals_clamps_the_limit(self, journal: DevJournal) -> None:
        for index in range(3):
            journal.create_effect(
                org_id=ORG,
                session_id=SESSION_ID,
                tool_name="delete_database",
                step_index=index + 1,
                args={},
                signature=f"sig-{index}",
                status="awaiting_approval",
                approval_id=f"approval-{index}",
            )

        assert len(journal.list_approvals(state="all", limit=2)) == 2
        assert len(journal.list_approvals(state="all", limit=0)) == 3

    def test_an_in_memory_journal_outlives_one_connection(self) -> None:
        storage = DevJournal(":memory:")
        try:
            effect_id = storage.create_effect(
                org_id=ORG,
                session_id=SESSION_ID,
                tool_name="send_email",
                step_index=1,
                args={},
                signature="sig-1",
                status="executing",
            )
            assert effect_id is not None
            # Every request opens its own connection, so the row must
            # be visible to a connection the journal did not hand out.
            assert storage.find_live_effect("sig-1") is not None
        finally:
            storage.close()

    def test_close_releases_the_in_memory_anchor(self) -> None:
        storage = DevJournal(":memory:")
        storage.close()
        storage.close()


# ── lifecycle ──────────────────────────────────────────────────────────────


class TestServerLifecycle:
    """Verify the server binds, answers, and releases its port."""

    def test_defaults_match_the_sdk_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both halves of an unconfigured run have to meet.

        The client dials the host in its default URL and the server
        binds an address, so the default run only works when the
        client's hostname resolves to an address the server listens
        on.
        """
        monkeypatch.delenv("UNDOLOG_PROXY_URL", raising=False)

        default = urlparse(_default_proxy_url())

        assert default.port == DEFAULT_PORT
        assert default.hostname is not None
        resolved = {
            address[0]
            for _, _, _, _, address in socket.getaddrinfo(
                default.hostname, DEFAULT_PORT
            )
        }
        assert DEFAULT_HOST in resolved
        assert DEFAULT_DB_PATH == "undolog-dev.db"

    async def test_url_reports_the_bound_port(self, tmp_path: Path) -> None:
        picked = DevServer(port=0, db_path=str(tmp_path / "ephemeral.db"))
        try:
            url = urlparse(picked.url)
            assert url.hostname == DEFAULT_HOST
            assert url.port is not None
            assert url.port != 0
        finally:
            picked.stop()

    async def test_start_serves_and_stop_releases_the_port(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.get("/health")
            assert response.status_code == 200

        bound = urlparse(server.url).port
        server.stop()

        with closing(socket.socket()) as probe:
            with pytest.raises(OSError):
                probe.connect((DEFAULT_HOST, bound))

    async def test_a_taken_port_fails_at_startup(self, tmp_path: Path) -> None:
        holder, port = _occupied_port()
        try:
            with pytest.raises(OSError):
                DevServer(port=port, db_path=str(tmp_path / "unused.db"))
        finally:
            holder.close()


# ── routing and errors ─────────────────────────────────────────────────────


class TestRouting:
    """Verify the routes, their methods, and the proxy's error envelope."""

    async def test_health_reports_ok(self, server: DevServer) -> None:
        async with _http(server) as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "undolog-dev"}

    async def test_unknown_path_is_not_found(self, server: DevServer) -> None:
        async with _http(server) as client:
            response = await client.get("/nope")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_wrong_method_is_not_allowed(self, server: DevServer) -> None:
        async with _http(server) as client:
            response = await client.get("/mcp/tool_call")

        assert response.status_code == 405
        assert response.json()["code"] == "method_not_allowed"
        assert response.json()["message"] == "POST required"

    async def test_errors_carry_a_request_id_and_timestamp(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.get("/nope")

        body = response.json()
        assert response.headers["X-Request-Id"] == body["request_id"]
        assert body["timestamp"].endswith("+00:00")

    async def test_a_caller_supplied_request_id_is_ignored(
        self,
        server: DevServer,
    ) -> None:
        """The reply carries the identifier the server generated.

        The proxy overwrites an inbound ``X-Request-Id``, so a local
        request does too and the caller's value never reaches the
        response.
        """
        async with _http(server) as client:
            response = await client.get(
                "/health",
                headers={"X-Request-Id": "trace-123"},
            )

        answered = response.headers["X-Request-Id"]
        assert answered != "trace-123"
        assert _UUID_PATTERN.fullmatch(answered)

    async def test_a_line_break_in_the_identifier_is_replaced(
        self,
        server: DevServer,
    ) -> None:
        """A folded header cannot put its second line on the response.

        The request parser keeps the line break that folds a header
        value, and a response line built from that value would carry
        it verbatim, so the reply's identifier is generated instead
        of taken from the request.
        """
        request = (
            b"GET /health HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n"
            b"X-Request-Id: trace-1\r\n Injected: evil\r\n"
            b"\r\n"
        )

        response = _raw_request(server, request)

        assert response.startswith("HTTP/1.1 200")
        assert "Injected" not in response
        answered = next(
            line.split(": ", 1)[1]
            for line in response.splitlines()
            if line.startswith("X-Request-Id: ")
        )
        assert _UUID_PATTERN.fullmatch(answered)


class TestErrorHandling:
    """Verify an unexpected failure reaches the logger instead of stderr."""

    def test_a_client_disconnect_is_logged_quietly(
        self,
        bare_http: _DevHttpServer,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A dropped event stream is routine, so it is only a debug line."""
        with caplog.at_level(logging.DEBUG, logger="undolog_sdk.dev"):
            try:
                raise ConnectionResetError("peer gone")
            except ConnectionResetError:
                bare_http.handle_error(None, ("127.0.0.1", 5555))

        assert any("disconnected" in record.message for record in caplog.records)
        assert "-" * 40 not in capsys.readouterr().err

    def test_an_unexpected_failure_is_logged_not_printed(
        self,
        bare_http: _DevHttpServer,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The traceback reaches the log, and nothing is written to stderr."""
        with caplog.at_level(logging.ERROR, logger="undolog_sdk.dev"):
            try:
                raise RuntimeError("journal write failed")
            except RuntimeError:
                bare_http.handle_error(None, ("127.0.0.1", 5555))

        assert any(
            record.levelno == logging.ERROR and "5555" in record.message
            for record in caplog.records
        )
        # socketserver brackets a traceback with a rule of dashes when it
        # prints, and the SDK never writes to stderr directly.
        assert "-" * 40 not in capsys.readouterr().err


# ── intercept contract ─────────────────────────────────────────────────────


class TestToolCall:
    """Verify ``POST /mcp/tool_call`` routes each call correctly."""

    async def test_a_new_call_executes(self, server: DevServer) -> None:
        async with _http(server) as client:
            response = await client.post("/mcp/tool_call", json=_tool_call_body())

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "executed"
        assert body["effect_id"]
        # The local server never runs the tool: the caller does, and
        # reports the outcome through commit or fail.
        assert body["result"] is None

    async def test_a_committed_call_replays(self, server: DevServer) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            effect_id = first.json()["effect_id"]
            await client.put(
                f"/effects/{effect_id}/commit",
                json={
                    "session_id": SESSION_ID,
                    "result": {"success": True, "output": {"status": "sent"}},
                },
            )
            second = await client.post("/mcp/tool_call", json=_tool_call_body())

        assert second.status_code == 200
        body = second.json()
        assert body["status"] == "replayed"
        assert body["effect_id"] == effect_id
        assert body["result"] == {"success": True, "output": {"status": "sent"}}

    async def test_a_different_step_index_is_a_separate_call(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            first = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(step_index=1),
            )
            second = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(step_index=2),
            )

        assert first.json()["status"] == "executed"
        assert second.json()["status"] == "executed"
        assert first.json()["effect_id"] != second.json()["effect_id"]

    async def test_a_different_session_is_a_separate_call(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            second = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(session_id="22222222-2222-2222-2222-222222222222"),
            )

        assert first.json()["effect_id"] != second.json()["effect_id"]

    async def test_a_missing_session_id_is_rejected(self, server: DevServer) -> None:
        body = _tool_call_body()
        del body["session_id"]

        async with _http(server) as client:
            response = await client.post("/mcp/tool_call", json=body)

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_request"
        assert "session_id and tool_name are required" in response.json()["message"]

    async def test_a_missing_tool_name_is_rejected(self, server: DevServer) -> None:
        body = _tool_call_body()
        del body["tool_name"]

        async with _http(server) as client:
            response = await client.post("/mcp/tool_call", json=body)

        assert response.status_code == 400
        assert "session_id and tool_name are required" in response.json()["message"]

    async def test_a_session_id_that_is_not_a_uuid_is_rejected(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(session_id="not-a-uuid"),
            )

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_request"

    @pytest.mark.parametrize("step_index", [-1, "1", None, True])
    async def test_a_step_index_that_is_not_a_counting_number_is_rejected(
        self,
        server: DevServer,
        step_index: Any,
    ) -> None:
        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(step_index=step_index),
            )

        assert response.status_code == 400
        assert response.json()["code"] == "invalid_request"

    async def test_args_that_are_not_an_object_are_rejected(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(args=["not", "an", "object"]),
            )

        assert response.status_code == 400
        assert response.json()["message"] == "args must be a JSON object"

    async def test_a_body_that_is_not_json_is_rejected(self, server: DevServer) -> None:
        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 400
        assert response.json()["message"] == "invalid JSON body"

    async def test_a_body_that_is_not_an_object_is_rejected(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=[{"session_id": SESSION_ID}],
            )

        assert response.status_code == 400
        assert response.json()["message"] == "request body must be a JSON object"

    async def test_an_oversized_body_is_refused(self, server: DevServer) -> None:
        body = _tool_call_body(args={"blob": "x" * (1024 * 1024 + 64)})

        async with _http(server) as client:
            response = await client.post("/mcp/tool_call", json=body)

        assert response.status_code == 413
        assert response.json()["code"] == "body_too_large"

    async def test_a_chunked_body_is_intercepted(self, server: DevServer) -> None:
        """A body streamed without a length follows the same rules."""
        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                content=_chunked(json.dumps(_tool_call_body()).encode()),
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "executed"

    async def test_an_oversized_chunked_body_is_refused(
        self, server: DevServer
    ) -> None:
        """A streamed body past the ceiling is refused without being kept."""
        oversized = b'{"blob": "' + b"x" * (1024 * 1024 + 64) + b'"}'

        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                content=_chunked(oversized),
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 413
        assert response.json()["code"] == "body_too_large"

    async def test_a_malformed_chunk_is_refused(self, server: DevServer) -> None:
        """A chunk size that is not hexadecimal is answered, not parsed."""
        request = (
            b"POST /mcp/tool_call HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"GARBAGE\r\n"
        )

        response = _raw_request(server, request)

        assert response.startswith("HTTP/1.1 400")
        assert "invalid chunk size" in response

    async def test_a_body_that_ends_early_is_refused(self, server: DevServer) -> None:
        """A chunk that never arrives is answered once the client hangs up."""
        request = (
            b"POST /mcp/tool_call HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5\r\nhe"
        )

        response = _raw_request(server, request, hang_up=True)

        assert response.startswith("HTTP/1.1 400")
        assert "truncated request body" in response

    async def test_a_connection_survives_a_refused_body(
        self, server: DevServer
    ) -> None:
        """A refused body is drained, so the same client carries on.

        A response written while the client is still sending is lost
        when the connection resets, so oversized bodies are read and
        discarded on the way to the answer.
        """
        oversized = _tool_call_body(args={"blob": "x" * (1024 * 1024 + 64)})

        async with _http(server) as client:
            refused = await client.post("/mcp/tool_call", json=oversized)
            accepted = await client.post(
                "/mcp/tool_call", json=_tool_call_body(step_index=2)
            )

        assert refused.status_code == 413
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "executed"

    async def test_a_refusal_drains_the_body_before_answering(
        self, server: DevServer
    ) -> None:
        """The refused bytes are read out, so the socket stays in step.

        Answering while those bytes are still arriving leaves them to
        be read as though they were the next request, and the pending
        data closes the connection before the reply is received.
        """
        payload = json.dumps(
            _tool_call_body(args={"blob": "x" * (_MAX_BODY_BYTES + 1)})
        ).encode("utf-8")
        request = (
            b"POST /mcp/tool_call HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n"
            b"\r\n" + payload
        )
        port = urlparse(server.url).port

        with socket.create_connection((DEFAULT_HOST, port), timeout=5) as sock:
            sock.sendall(request)
            refused = _read_one_response(sock)
            sock.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            healthy = _read_one_response(sock)

        assert refused.startswith("HTTP/1.1 413")
        assert "connection: close" not in refused.lower()
        assert healthy.startswith("HTTP/1.1 200")

    async def test_the_signature_matches_the_sdk(self, server: DevServer) -> None:
        """Replay depends on the server hashing calls the way the SDK does."""
        from undolog_sdk.signature import call_signature

        args = {"to": "alice@example.com"}
        expected = call_signature(SESSION_ID, 1, "send_email", args)

        async with _http(server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(args=args),
            )
        effect_id = response.json()["effect_id"]

        with closing(sqlite3.connect(server.db_path)) as conn:
            row = conn.execute(
                "SELECT signature FROM effects WHERE id = ?",
                (effect_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == expected


# ── reported outcomes ──────────────────────────────────────────────────────


class TestEffectOutcomes:
    """Verify ``commit`` and ``fail`` record what the caller reports."""

    async def test_commit_makes_the_call_replayable(self, server: DevServer) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            effect_id = first.json()["effect_id"]
            response = await client.put(
                f"/effects/{effect_id}/commit",
                json={"session_id": SESSION_ID, "result": {"success": True}},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "committed", "effect_id": effect_id}

    async def test_commit_to_an_unknown_effect_is_not_found(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.put(
                "/effects/missing-effect/commit",
                json={"session_id": SESSION_ID, "result": {}},
            )

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_fail_frees_the_signature_for_a_retry(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            failed_id = first.json()["effect_id"]
            await client.put(
                f"/effects/{failed_id}/fail",
                json={"session_id": SESSION_ID, "error": "smtp unavailable"},
            )
            second = await client.post("/mcp/tool_call", json=_tool_call_body())

        assert second.json()["status"] == "executed"
        assert second.json()["effect_id"] != failed_id

    async def test_fail_to_an_unknown_effect_is_not_found(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            response = await client.put(
                "/effects/missing-effect/fail",
                json={"session_id": SESSION_ID, "error": "boom"},
            )

        assert response.status_code == 404

    async def test_an_error_that_is_not_a_string_is_rejected(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            effect_id = first.json()["effect_id"]
            response = await client.put(
                f"/effects/{effect_id}/fail",
                json={"session_id": SESSION_ID, "error": {"why": "boom"}},
            )

        assert response.status_code == 400
        assert response.json()["message"] == "error must be a string"

    async def test_the_client_treats_a_missing_effect_as_a_no_op(
        self,
        server: DevServer,
    ) -> None:
        """The SDK treats 404 as a no-op, so a local 404 is not fatal."""
        async with _sdk(server) as client:
            committed = await client.commit(
                org_id=ORG,
                session_id=SESSION_ID,
                effect_id="missing-effect",
                result={"success": True},
            )
            failed = await client.fail(
                org_id=ORG,
                session_id=SESSION_ID,
                effect_id="missing-effect",
                error="boom",
            )

        assert committed == {}
        assert failed == {}


# ── approvals ──────────────────────────────────────────────────────────────


class TestApprovals:
    """Verify held calls, their resolution, and the approval list."""

    async def test_a_gated_call_waits_for_approval(
        self, gated_server: DevServer
    ) -> None:
        async with _http(gated_server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["approval_id"]
        assert body["retry_after"] == 5

    async def test_a_ungated_call_still_executes(self, gated_server: DevServer) -> None:
        async with _http(gated_server) as client:
            response = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="send_email"),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "executed"

    async def test_a_held_call_reports_the_same_approval(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            first = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )
            second = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )

        assert first.json()["approval_id"] == second.json()["approval_id"]

    async def test_granting_an_approval_journals_a_marked_mock(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            held = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(
                    tool_name="delete_database",
                    args={"db": "prod"},
                ),
            )
            approval_id = held.json()["approval_id"]
            response = await client.post(f"/approvals/{approval_id}/approve", json={})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["execution"] == "committed"
        assert body["approval_id"] == approval_id
        assert body["result"]["success"] is True
        assert body["result"]["output"] == {
            "mock": True,
            "tool_name": "delete_database",
            "args": {"db": "prod"},
        }

    async def test_an_approved_call_replays_the_mock_result(
        self,
        gated_server: DevServer,
    ) -> None:
        body = _tool_call_body(tool_name="delete_database")
        async with _http(gated_server) as client:
            held = await client.post("/mcp/tool_call", json=body)
            await client.post(
                f"/approvals/{held.json()['approval_id']}/approve",
                json={},
            )
            replayed = await client.post("/mcp/tool_call", json=body)

        assert replayed.json()["status"] == "replayed"
        assert replayed.json()["result"]["output"]["mock"] is True

    async def test_a_refused_call_asks_again(
        self,
        gated_server: DevServer,
    ) -> None:
        body = _tool_call_body(tool_name="delete_database")
        async with _http(gated_server) as client:
            held = await client.post("/mcp/tool_call", json=body)
            approval_id = held.json()["approval_id"]
            rejected = await client.post(
                f"/approvals/{approval_id}/reject",
                json={},
            )
            again = await client.post("/mcp/tool_call", json=body)

        assert rejected.json() == {"status": "rejected", "approval_id": approval_id}
        assert again.status_code == 202
        assert again.json()["approval_id"] != approval_id

    async def test_resolving_twice_conflicts(self, gated_server: DevServer) -> None:
        async with _http(gated_server) as client:
            held = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )
            approval_id = held.json()["approval_id"]
            await client.post(f"/approvals/{approval_id}/approve", json={})
            second = await client.post(f"/approvals/{approval_id}/approve", json={})

        assert second.status_code == 409
        assert second.json()["code"] == "conflict"
        assert second.json()["message"] == "approval already resolved"

    async def test_an_unknown_approval_is_not_found(
        self, gated_server: DevServer
    ) -> None:
        async with _http(gated_server) as client:
            response = await client.post("/approvals/missing/approve", json={})

        assert response.status_code == 404
        assert response.json()["message"] == "approval not found"

    async def test_a_malformed_approval_path_is_not_found(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            response = await client.post("/approvals/missing/settle", json={})

        assert response.status_code == 404

    async def test_the_list_returns_pending_approvals(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )
            response = await client.get("/approvals")

        assert response.status_code == 200
        approvals = response.json()
        assert len(approvals) == 1
        assert approvals[0]["status"] == "pending"
        assert approvals[0]["tool_name"] == "delete_database"
        # No organisation header was sent, so the call was recorded
        # under the local fallback.
        assert approvals[0]["org_id"] == "local"

    async def test_the_list_filters_by_state(self, gated_server: DevServer) -> None:
        async with _http(gated_server) as client:
            held = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )
            await client.post(
                f"/approvals/{held.json()['approval_id']}/approve",
                json={},
            )
            pending = await client.get("/approvals?state=pending")
            approved = await client.get("/approvals?state=approved")
            everything = await client.get("/approvals?state=all")

        assert pending.json() == []
        assert len(approved.json()) == 1
        assert len(everything.json()) == 1

    async def test_an_unknown_state_is_rejected(self, gated_server: DevServer) -> None:
        async with _http(gated_server) as client:
            response = await client.get("/approvals?state=whatever")

        assert response.status_code == 400
        assert response.json()["message"] == "invalid state"

    async def test_a_limit_that_is_not_a_number_is_rejected(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            response = await client.get("/approvals?limit=many")

        assert response.status_code == 400
        assert response.json()["message"] == "invalid limit"

    async def test_a_negative_limit_uses_the_default(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            response = await client.get("/approvals?limit=-5")

        assert response.status_code == 200
        assert response.json() == []


# ── event stream ───────────────────────────────────────────────────────────


class TestEventStream:
    """Verify the Server-Sent Events stream the dashboard consumes."""

    async def test_the_stream_uses_the_proxy_media_type(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            async with client.stream("GET", "/events") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert response.headers["cache-control"] == "no-cache"

    async def test_a_lifecycle_emits_intercepted_then_committed(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            async with _event_stream(client, expected=2) as events:
                first = await client.post(
                    "/mcp/tool_call",
                    json=_tool_call_body(),
                )
                await client.put(
                    f"/effects/{first.json()['effect_id']}/commit",
                    json={"session_id": SESSION_ID, "result": {"success": True}},
                )

        assert [event["type"] for event in events] == [
            "effect_intercepted",
            "effect_committed",
        ]
        intercepted, committed = events
        # No organisation header was sent, so the call was recorded
        # under the local fallback.
        assert intercepted["org_id"] == "local"
        assert intercepted["session_id"] == SESSION_ID
        assert intercepted["payload"] == {"stage": "intercepted"}
        assert committed["effect_id"] == first.json()["effect_id"]
        assert committed["timestamp"].endswith("+00:00")

    async def test_a_replay_is_reported(self, server: DevServer) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            await client.put(
                f"/effects/{first.json()['effect_id']}/commit",
                json={"session_id": SESSION_ID, "result": {"success": True}},
            )
            async with _event_stream(client, expected=2) as events:
                await client.post("/mcp/tool_call", json=_tool_call_body())

        assert [event["type"] for event in events] == [
            "effect_intercepted",
            "effect_replayed",
        ]

    async def test_a_failure_is_reported(self, server: DevServer) -> None:
        async with _http(server) as client:
            first = await client.post("/mcp/tool_call", json=_tool_call_body())
            async with _event_stream(client, expected=1) as events:
                await client.put(
                    f"/effects/{first.json()['effect_id']}/fail",
                    json={"session_id": SESSION_ID, "error": "boom"},
                )

        assert events[0]["type"] == "effect_failed"
        assert events[0]["payload"] == {"stage": "execute", "error": "failed"}

    async def test_a_held_call_reports_the_approval(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            async with _event_stream(client, expected=2) as events:
                await client.post(
                    "/mcp/tool_call",
                    json=_tool_call_body(tool_name="delete_database"),
                )

        assert events[-1]["type"] == "approval_required"
        assert events[-1]["approval_id"]

    async def test_resolving_an_approval_is_reported(
        self,
        gated_server: DevServer,
    ) -> None:
        async with _http(gated_server) as client:
            held = await client.post(
                "/mcp/tool_call",
                json=_tool_call_body(tool_name="delete_database"),
            )
            approval_id = held.json()["approval_id"]
            async with _event_stream(client, expected=1) as events:
                await client.post(f"/approvals/{approval_id}/approve", json={})

        assert events[0]["type"] == "approval_approved"
        assert events[0]["approval_id"] == approval_id
        assert events[0]["payload"]["status"] == "approved"

    async def test_a_heartbeat_keeps_an_idle_stream_open(
        self,
        server: DevServer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(dev_mod, "_SSE_HEARTBEAT_SECONDS", 0.05)
        lines: list[str] = []
        connected = asyncio.Event()

        async def _reader() -> None:
            async with _http(server) as client:
                async with client.stream("GET", "/events") as response:
                    connected.set()
                    async for line in response.aiter_lines():
                        lines.append(line)
                        if len(lines) >= 3:
                            return

        task = asyncio.create_task(_reader())
        try:
            await asyncio.wait_for(connected.wait(), timeout=5.0)
            await asyncio.wait_for(task, timeout=5.0)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        assert any(line.startswith(": ping") for line in lines)

    async def test_a_disconnecting_subscriber_is_tolerated(
        self,
        server: DevServer,
    ) -> None:
        async with _http(server) as client:
            async with client.stream("GET", "/events") as response:
                assert response.status_code == 200
            health = await client.get("/health")

        assert health.status_code == 200

    async def test_events_are_not_filtered_by_organisation(
        self,
        server: DevServer,
    ) -> None:
        """The local server is single-tenant, so any subscriber sees all."""
        seen: list[dict[str, Any]] = []
        connected = asyncio.Event()

        async def _reader() -> None:
            async with _http(server) as client:
                async with client.stream(
                    "GET",
                    "/events",
                    headers={"X-UndoLog-Org-Id": "org_other"},
                ) as response:
                    connected.set()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            seen.append(json.loads(line[len("data: ") :]))
                            return

        task = asyncio.create_task(_reader())
        try:
            await asyncio.wait_for(connected.wait(), timeout=5.0)
            async with _http(server) as client:
                await client.post("/mcp/tool_call", json=_tool_call_body())
            await asyncio.wait_for(task, timeout=5.0)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        assert seen and seen[0]["type"] == "effect_intercepted"


# ── decorator lifecycle ────────────────────────────────────────────────────


class TestDecoratorLifecycle:
    """Verify a decorated tool completes its whole lifecycle locally."""

    async def test_a_compensable_tool_executes_once_then_replays(
        self,
        server: DevServer,
    ) -> None:
        """A resumed run replays what its earlier run already journaled."""
        calls: list[str] = []

        async with _sdk(server) as client:

            @undolog_tool(
                ToolTier.COMPENSABLE,
                compensation=CompensationDescriptor.new("undo_send_email"),
                client=client,
            )
            async def send_email(to: str) -> dict[str, Any]:
                calls.append(to)
                return {"status": "sent", "to": to}

            async with UndoLogSession(org_id=ORG) as session:
                session_id = session.session_id
                async with run_with_session(session):
                    first = await send_email(to="alice@example.com")

            # A resumed run reproduces the same session and step, which
            # is what makes the earlier call replay rather than run again.
            resumed = UndoLogSession(org_id=ORG, session_id=session_id)
            async with run_with_session(resumed):
                second = await send_email(to="alice@example.com")

        assert first == {"status": "sent", "to": "alice@example.com"}
        assert second == first
        assert calls == ["alice@example.com"]

    async def test_two_calls_in_one_session_are_separate_effects(
        self,
        server: DevServer,
    ) -> None:
        """Step order is part of a call's identity, so a second call runs."""
        calls: list[str] = []

        async with _sdk(server) as client:

            @undolog_tool(
                ToolTier.COMPENSABLE,
                compensation=CompensationDescriptor.new("undo_send_email"),
                client=client,
            )
            async def send_email(to: str) -> dict[str, Any]:
                calls.append(to)
                return {"status": "sent", "to": to}

            async with UndoLogSession(org_id=ORG) as session:
                async with run_with_session(session):
                    await send_email(to="alice@example.com")
                    await send_email(to="bob@example.com")

        assert calls == ["alice@example.com", "bob@example.com"]
        with closing(sqlite3.connect(server.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM effects").fetchone()
        assert count is not None
        assert count[0] == 2

    async def test_a_failed_tool_runs_again_on_retry(
        self,
        server: DevServer,
    ) -> None:
        attempts: list[str] = []

        async with _sdk(server) as client:

            @undolog_tool(
                ToolTier.COMPENSABLE,
                compensation=CompensationDescriptor.new("undo_send_email"),
                client=client,
            )
            async def send_email(to: str) -> dict[str, Any]:
                attempts.append(to)
                if len(attempts) == 1:
                    raise RuntimeError("smtp unavailable")
                return {"status": "sent"}

            async with UndoLogSession(org_id=ORG) as session:
                session_id = session.session_id
                async with run_with_session(session):
                    with pytest.raises(RuntimeError, match="smtp unavailable"):
                        await send_email(to="alice@example.com")

            # Retrying the same call executes it, because a failure is
            # kept as history and never cached as a result to replay.
            retry = UndoLogSession(org_id=ORG, session_id=session_id)
            async with run_with_session(retry):
                retried = await send_email(to="alice@example.com")

        assert retried == {"status": "sent"}
        assert len(attempts) == 2

    async def test_a_safe_tool_never_reaches_the_server(
        self,
        server: DevServer,
    ) -> None:
        @undolog_tool(ToolTier.SAFE)
        async def search(query: str) -> str:
            return f"results for {query}"

        async with UndoLogSession(org_id=ORG) as session:
            async with run_with_session(session):
                assert await search(query="undolog") == "results for undolog"

        with closing(sqlite3.connect(server.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM effects").fetchone()

        assert count is not None
        assert count[0] == 0

    async def test_an_irreversible_tool_waits_then_replays_the_mock(
        self,
        gated_server: DevServer,
    ) -> None:
        calls: list[str] = []

        async with _sdk(gated_server) as client:

            @undolog_tool(ToolTier.IRREVERSIBLE, client=client)
            async def delete_database(db: str) -> dict[str, Any]:
                calls.append(db)
                return {"status": "deleted", "db": db}

            async with UndoLogSession(org_id=ORG) as session:
                async with run_with_session(session):
                    with pytest.raises(AwaitingApprovalError) as raised:
                        await delete_database(db="prod")
                    approval_id = raised.value.approval_id
                    await client.approve(org_id=ORG, approval_id=approval_id)

            # A resumed run reproduces the same session and steps, so
            # the approved call reaches the journal at the same
            # position and replays what the approval recorded.
            resumed_session = UndoLogSession(
                org_id=ORG,
                session_id=session.session_id,
            )
            async with run_with_session(resumed_session):
                resumed = await delete_database(db="prod")

        assert approval_id
        assert resumed["mock"] is True
        assert calls == []

    async def test_a_tool_without_a_session_is_refused(
        self,
        server: DevServer,
    ) -> None:
        async with _sdk(server) as client:

            @undolog_tool(
                ToolTier.COMPENSABLE,
                compensation=CompensationDescriptor.new("undo_send_email"),
                client=client,
            )
            async def send_email(to: str) -> str:
                return to

            with pytest.raises(RuntimeError, match="requires a session"):
                await send_email(to="alice@example.com")


# ── command line ───────────────────────────────────────────────────────────


class TestCommandLine:
    """Verify the ``undolog dev`` entry point."""

    def test_the_parser_defaults_to_the_sdk_port(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["dev"])

        assert args.command == "dev"
        assert args.host == DEFAULT_HOST
        assert args.port == DEFAULT_PORT
        assert args.db == DEFAULT_DB_PATH
        assert args.irreversible == []

    def test_irreversible_patterns_accumulate(self) -> None:
        args = build_parser().parse_args(
            ["dev", "--irreversible", "delete_*", "--irreversible", "wire_*"]
        )

        assert args.irreversible == ["delete_*", "wire_*"]

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_help_documents_the_limitations(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(SystemExit) as raised:
            main(["dev", "--help"])

        # argparse wraps help text, so compare against normalised spaces.
        output = " ".join(capsys.readouterr().out.split())
        assert raised.value.code == 0
        assert "no authentication" in output
        assert "no row-level security" in output
        assert "no advisory locks" in output
        assert "not suitable for production" in output
        assert "--irreversible" in output
        assert "mock" in output

    def test_a_taken_port_is_reported(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        holder, port = _occupied_port()
        try:
            with caplog.at_level(logging.ERROR, logger="undolog_sdk.dev"):
                exit_code = main(
                    ["dev", "--port", str(port), "--db", str(tmp_path / "unused.db")]
                )
        finally:
            holder.close()

        assert exit_code == 1
        assert any("cannot listen on" in record.message for record in caplog.records)

    def test_an_unopenable_journal_is_reported(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="undolog_sdk.dev"):
            exit_code = main(["dev", "--db", "/missing/directory/journal.db"])

        assert exit_code == 1
        assert any(
            "cannot open the journal" in record.message for record in caplog.records
        )

    def test_the_parser_is_an_argparse_parser(self) -> None:
        assert isinstance(build_parser(), argparse.ArgumentParser)
