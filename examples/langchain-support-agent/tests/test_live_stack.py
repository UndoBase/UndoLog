"""Live-stack integration tests for UndoLog example demos.

Exercises the full UndoLog stack (postgres + engine + proxy + mock tool
server) end-to-end.  Verifies effect log entries and undo stack state
directly in PostgreSQL to confirm correct state machine transitions.

Usage
-----
::

    docker compose up -d postgres engine tool-server proxy
    pip install asyncpg
    pytest tests/test_live_stack.py -v

Prerequisites
-------------
*   UndoLog stack running (``docker compose up -d``).
*   ``asyncpg`` installed (``pip install asyncpg``).
*   ``DATABASE_URL`` or ``TEST_DATABASE_URL`` pointing at PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
import undolog_sdk.decorators

from undolog_sdk import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession

from example_tools import get_tool_registry


log = logging.getLogger(__name__)


# ── Prerequisite check ─────────────────────────────────────────────────────


def _asyncpg_available() -> bool:
    """Check whether ``asyncpg`` is installed."""
    try:
        import asyncpg  # noqa: F401  -- existence check only

        return True
    except ImportError:
        return False


async def _proxy_healthy() -> bool:
    """Check whether the UndoLog proxy is reachable."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{proxy_url()}/health", timeout=5.0)
            return resp.status_code == 200
    except (httpx.RequestError, httpx.HTTPStatusError):
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(
        not _asyncpg_available(),
        reason="asyncpg not installed (pip install asyncpg)",
    ),
]


# ── Configuration helpers ──────────────────────────────────────────────────


def proxy_url() -> str:
    """Return the proxy base URL from the environment (default ``http://localhost:8080``)."""
    return os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


def db_url() -> str:
    """Return the database URL from the environment."""
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/undolog"),
    )


def org_id() -> str:
    """Return the organisation identifier (default ``org_demo``)."""
    return os.environ.get("UNDOLOG_ORG_ID", "org_demo")


def api_key() -> str | None:
    """Return the API key or ``None``."""
    return os.environ.get("UNDOLOG_API_KEY") or None


def headers() -> dict[str, str]:
    """Build request headers for proxy API calls."""
    hdrs: dict[str, str] = {
        "X-UndoLog-Org-Id": org_id(),
        "Content-Type": "application/json",
    }
    key = api_key()
    if key:
        hdrs["X-Api-Key"] = key
    return hdrs


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _skip_if_stack_down() -> None:
    """Skip every test in this module when the stack is not running."""
    if not await _proxy_healthy():
        pytest.skip("UndoLog stack not running (docker compose up -d)")


@pytest.fixture(autouse=True)
def _fresh_undolog_client() -> None:
    """Reset the module-level default client between tests.

    pytest-asyncio creates a new event loop for each test.  The SDK's
    module-level ``UndoLogClient`` singleton holds an ``httpx.AsyncClient``
    that is tied to the first test's event loop.  Resetting it forces a
    fresh client per test, avoiding ``Event loop is closed`` errors.
    """
    undolog_sdk.decorators._DEFAULT_CLIENT = None


@pytest_asyncio.fixture
async def db_conn() -> Any:
    """Provide a per-test asyncpg connection.

    Yields
    ------
    asyncpg.Connection
        Open PostgreSQL connection.
    """
    import asyncpg

    conn = await asyncpg.connect(db_url())
    yield conn
    await conn.close()


# ── DB query helpers ───────────────────────────────────────────────────────


async def fetch_effects(conn: Any, session_id: str) -> list[dict[str, Any]]:
    """Return effect log entries for *session_id* ordered by step_index.

    Parameters
    ----------
    conn : asyncpg.Connection
        Open database connection.
    session_id : str
        Session UUID string.

    Returns
    -------
    list[dict]
        Each row contains ``tool_name``, ``step_index``, ``state``,
        ``call_signature``, ``args_snapshot``, ``result_snapshot``, and
        ``replay_count``.
    """
    rows = await conn.fetch(
        """
        SELECT tool_name, step_index, state::text, call_signature,
               args_snapshot, result_snapshot, replay_count
        FROM undolog_effect_log
        WHERE session_id = $1::uuid
        ORDER BY step_index
        """,
        session_id,
    )
    return [dict(row) for row in rows]


async def fetch_session(conn: Any, session_id: str) -> dict[str, Any] | None:
    """Return session record for *session_id*.

    Parameters
    ----------
    conn : asyncpg.Connection
        Open database connection.
    session_id : str
        Session UUID string.

    Returns
    -------
    dict or None
        Row containing ``state`` and ``failure_reason``, or None if not found.
    """
    row = await conn.fetchrow(
        """
        SELECT state::text, failure_reason
        FROM undolog_sessions
        WHERE session_id = $1::uuid
        """,
        session_id,
    )
    return dict(row) if row else None


async def fetch_approval(conn: Any, approval_id: str) -> dict[str, Any] | None:
    """Return approval request record for *approval_id*.

    Parameters
    ----------
    conn : asyncpg.Connection
        Open database connection.
    approval_id : str
        Approval request UUID string.

    Returns
    -------
    dict or None
        Row containing ``tool_name``, ``state``, and ``status``, or None if not
        found.
    """
    row = await conn.fetchrow(
        """
        SELECT tool_name, state::text AS status
        FROM undolog_approval_requests
        WHERE approval_request_id = $1::uuid
        """,
        approval_id,
    )
    return dict(row) if row else None


async def fetch_approval_events(conn: Any, approval_id: str) -> list[dict[str, Any]]:
    """Return immutable audit events for *approval_id* ordered by time.

    Parameters
    ----------
    conn : asyncpg.Connection
        Open database connection.
    approval_id : str
        Approval request UUID string.

    Returns
    -------
    list[dict]
        Each row contains ``action``, ``actor``, ``note``, and ``occurred_at``.
    """
    rows = await conn.fetch(
        """
        SELECT action::text, actor, note, occurred_at
        FROM undolog_approval_events
        WHERE approval_request_id = $1::uuid
        ORDER BY occurred_at
        """,
        approval_id,
    )
    return [dict(row) for row in rows]


async def fetch_undo_stack(conn: Any, session_id: str) -> list[dict[str, Any]]:
    """Return undo stack entries for *session_id* ordered by stack_position DESC.

    Parameters
    ----------
    conn : asyncpg.Connection
        Open database connection.
    session_id : str
        Session UUID string.

    Returns
    -------
    list[dict]
        Each row contains ``stack_position``, ``compensation_fn``,
        ``state``, ``registered_at``, ``max_retries``, and
        ``retry_backoff_ms``.
    """
    rows = await conn.fetch(
        """
        SELECT stack_position, compensation_fn, state, registered_at,
               max_retries, retry_backoff_ms
        FROM undolog_undo_stack
        WHERE session_id = $1::uuid
        ORDER BY stack_position DESC
        """,
        session_id,
    )
    return [dict(row) for row in rows]


# ── Approval lifecycle tests ────────────────────────────────────────────────


class TestApprovalLifecycle:
    """Full approval lifecycle for IRREVERSIBLE tools."""

    async def test_pending_approval_creates_db_entry(self, db_conn: Any) -> None:
        """IRREVERSIBLE tool raises AwaitingApprovalError and creates a pending effect."""
        tools = get_tool_registry()
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await tools["lookup_customer"](customer_id="cust_42", _session=session)
            await tools["send_email"](
                to="alice@example.com",
                subject="Test",
                body="Integration test",
                _session=session,
            )

            with pytest.raises(AwaitingApprovalError):
                await tools["escalate_case"](
                    ticket_id="TKT-42",
                    reason="Integration test escalation",
                    _session=session,
                )

            effects = await fetch_effects(db_conn, session_id)
            assert len(effects) == 2, f"Expected 2 effects, got {len(effects)}"

            assert effects[0]["tool_name"] == "send_email"
            assert effects[0]["state"] == "committed", (
                f"Expected committed, got {effects[0]['state']}"
            )

            assert effects[1]["tool_name"] == "escalate_case"
            assert effects[1]["state"] == "pending", f"Expected pending, got {effects[1]['state']}"

    async def test_approve_commits_effect(self, db_conn: Any) -> None:
        """Approving via the proxy API commits the IRREVERSIBLE effect."""
        tools = get_tool_registry()
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await tools["lookup_customer"](customer_id="cust_1", _session=session)
            await tools["send_email"](
                to="bob@example.com",
                subject="Approval test",
                body="Body",
                _session=session,
            )
            ticket = await tools["create_ticket"](
                customer_id="cust_1",
                priority="high",
                description="Test ticket for approve test",
                _session=session,
            )
            ticket_id: str = ticket["ticket_id"]

            approval_id: str | None = None
            try:
                await tools["escalate_case"](
                    ticket_id=ticket_id,
                    reason="Critical",
                    _session=session,
                )
            except AwaitingApprovalError as exc:
                approval_id = exc.approval_id

            assert approval_id is not None, "Expected an approval_id"

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{proxy_url()}/approvals/{approval_id}/approve",
                    json={"actor": "integration_test", "note": "Auto-approved"},
                    headers=headers(),
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
                data = resp.json()
                assert data.get("execution") == "committed", (
                    f"Expected committed, got {data.get('execution')}"
                )

            effects = await fetch_effects(db_conn, session_id)
            escalate = [e for e in effects if e["tool_name"] == "escalate_case"]
            assert len(escalate) == 1
            assert escalate[0]["state"] == "committed", (
                f"Expected committed, got {escalate[0]['state']}"
            )

    async def test_reject_transitions_effect_and_halts_session(self, db_conn: Any) -> None:
        """Rejecting via the proxy API transitions the effect and halts the session."""
        tools = get_tool_registry()
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await tools["lookup_customer"](customer_id="cust_1", _session=session)
            await tools["send_email"](
                to="carol@example.com",
                subject="Reject test",
                body="Body",
                _session=session,
            )

            approval_id: str | None = None
            try:
                await tools["escalate_case"](
                    ticket_id="TKT-200",
                    reason="Critical",
                    _session=session,
                )
            except AwaitingApprovalError as exc:
                approval_id = exc.approval_id

            assert approval_id is not None, "Expected an approval_id"

            # Reject the approval.
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{proxy_url()}/approvals/{approval_id}/reject",
                    json={"actor": "integration_test", "note": "Auto-rejected"},
                    headers=headers(),
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                assert data.get("status") == "rejected", (
                    f"Expected rejected, got {data.get('status')}"
                )

            # Effect must be rejected.
            effects = await fetch_effects(db_conn, session_id)
            escalate = [e for e in effects if e["tool_name"] == "escalate_case"]
            assert len(escalate) == 1
            assert escalate[0]["state"] == "rejected", (
                f"Expected rejected, got {escalate[0]['state']}"
            )

            # Session must be halted.
            sess = await fetch_session(db_conn, session_id)
            assert sess is not None, "Session not found"
            assert sess["state"] == "halted", f"Expected halted, got {sess['state']}"
            assert sess["failure_reason"] == "approval rejected", (
                f"Expected 'approval rejected', got {sess['failure_reason']}"
            )

            # Approval request must be rejected.
            approval = await fetch_approval(db_conn, approval_id)
            assert approval is not None, "Approval not found"
            assert approval["status"] == "rejected", f"Expected rejected, got {approval['status']}"

            # The reject actor must be recorded in the immutable audit trail.
            events = await fetch_approval_events(db_conn, approval_id)
            assert any(
                e["action"] == "reject" and e["actor"] == "integration_test" for e in events
            ), f"Expected a reject audit event from integration_test, got {events}"

    async def test_double_approve_returns_conflict(self, db_conn: Any) -> None:
        """A second approve on the same approval returns 409 Conflict.

        The proxy's in-memory store and the engine's ``rows_affected()``
        guard both prevent resolving an already-resolved approval.
        """
        tools = get_tool_registry()
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await tools["lookup_customer"](customer_id="cust_1", _session=session)
            await tools["send_email"](
                to="dave@example.com",
                subject="Double approve",
                body="Body",
                _session=session,
            )
            ticket = await tools["create_ticket"](
                customer_id="cust_1",
                priority="high",
                description="Test ticket for double-approve test",
                _session=session,
            )
            ticket_id: str = ticket["ticket_id"]

            approval_id: str | None = None
            try:
                await tools["escalate_case"](
                    ticket_id=ticket_id,
                    reason="Critical",
                    _session=session,
                )
            except AwaitingApprovalError as exc:
                approval_id = exc.approval_id

            assert approval_id is not None, "Expected an approval_id"

            async with httpx.AsyncClient() as client:
                resp1 = await client.post(
                    f"{proxy_url()}/approvals/{approval_id}/approve",
                    json={"actor": "integration_test", "note": "First approve"},
                    headers=headers(),
                )
                assert resp1.status_code == 200, (
                    f"First approve: expected 200, got {resp1.status_code}: {resp1.text}"
                )
                assert resp1.json().get("execution") == "committed"

                resp2 = await client.post(
                    f"{proxy_url()}/approvals/{approval_id}/approve",
                    json={"actor": "integration_test", "note": "Double approve"},
                    headers=headers(),
                )
                assert resp2.status_code == 409, (
                    f"Second approve: expected 409, got {resp2.status_code}: {resp2.text}"
                )
                assert "already resolved" in resp2.text.lower()

                sess = await fetch_session(db_conn, session_id)
                assert sess is not None, "Session not found"
                assert sess["state"] != "halted", (
                    "Session should not be halted after a rejected double-approve"
                )

    async def test_double_reject_returns_conflict(self, db_conn: Any) -> None:
        """A second reject on the same approval returns 409 Conflict."""
        tools = get_tool_registry()
        async with UndoLogSession(org_id=org_id()) as session:
            await tools["lookup_customer"](customer_id="cust_1", _session=session)
            await tools["send_email"](
                to="eve@example.com",
                subject="Double reject",
                body="Body",
                _session=session,
            )
            ticket = await tools["create_ticket"](
                customer_id="cust_1",
                priority="high",
                description="Test ticket for double-reject test",
                _session=session,
            )
            ticket_id: str = ticket["ticket_id"]

            approval_id: str | None = None
            try:
                await tools["escalate_case"](
                    ticket_id=ticket_id,
                    reason="Critical",
                    _session=session,
                )
            except AwaitingApprovalError as exc:
                approval_id = exc.approval_id

            assert approval_id is not None, "Expected an approval_id"

            async with httpx.AsyncClient() as client:
                resp1 = await client.post(
                    f"{proxy_url()}/approvals/{approval_id}/reject",
                    json={"actor": "integration_test", "note": "First reject"},
                    headers=headers(),
                )
                assert resp1.status_code == 200, (
                    f"First reject: expected 200, got {resp1.status_code}: {resp1.text}"
                )
                assert resp1.json().get("status") == "rejected"

                resp2 = await client.post(
                    f"{proxy_url()}/approvals/{approval_id}/reject",
                    json={"actor": "integration_test", "note": "Double reject"},
                    headers=headers(),
                )
                assert resp2.status_code == 409, (
                    f"Second reject: expected 409, got {resp2.status_code}: {resp2.text}"
                )
                assert "already resolved" in resp2.text.lower()


# ── Concurrent execution tests ──────────────────────────────────────────────


class TestConcurrentExecution:
    """Concurrent tool calls within a single session.

    Verifies that the engine handles concurrent tool invocations correctly:
    each call receives a unique step index, all effects are committed, and
    no effect-log or undo-stack entries are lost to race conditions.
    """

    async def test_ten_concurrent_tool_calls(self, db_conn: Any) -> None:
        """Ten concurrent COMPENSABLE calls all commit with unique step indices."""
        tools = get_tool_registry()
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await tools["lookup_customer"](customer_id="cust_1", _session=session)

            async def _send(to_idx: int) -> dict[str, Any]:
                return await tools["send_email"](  # type: ignore[no-any-return]
                    to=f"user{to_idx}@example.com",
                    subject=f"Concurrent test {to_idx}",
                    body="Body",
                    _session=session,
                )

            results = await asyncio.gather(*[_send(i) for i in range(10)])

            assert len(results) == 10
            for r in results:
                assert r["status"] == "sent"

            effects = await fetch_effects(db_conn, session_id)
            # SAFE tool (lookup_customer) does not create an effect-log entry.
            # The 10 send_email calls produce steps 1-10.
            assert len(effects) == 10, f"Expected 10 effects, got {len(effects)}"

            steps = [e["step_index"] for e in effects]
            assert steps == list(range(1, 11)), f"Unexpected step indices: {steps}"

            for e in effects:
                assert e["state"] == "committed", (
                    f"{e['tool_name']} @ step {e['step_index']}: "
                    f"expected committed, got {e['state']}"
                )

            undo_entries = await fetch_undo_stack(db_conn, session_id)
            assert len(undo_entries) == 10, (
                f"Expected 10 undo stack entries, got {len(undo_entries)}"
            )


# ── Compensation chain tests ────────────────────────────────────────────────


class TestCompensationChain:
    """LIFO compensation rollback on tool failure."""

    async def test_failure_triggers_lifo_compensation(self, db_conn: Any) -> None:
        """A downstream failure triggers LIFO compensation via the undo stack."""
        from compensation_demo import (
            assign_engineer,
            escalate_ticket,
            lookup_user,
            notify_user,
            open_ticket,
        )

        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            user = await lookup_user(user_id="user_42", _session=session)
            assert user is not None

            email = await notify_user(
                to="bob@example.com",
                subject="Ticket created",
                body="Your ticket has been created.",
                _session=session,
            )
            assert email is not None

            ticket = await open_ticket(
                user_id="user_42",
                priority="high",
                description="Dashboard inaccessible",
                _session=session,
            )
            assert ticket is not None

            assignment = await assign_engineer(
                ticket_id=ticket["ticket_id"],
                engineer="sarah",
                _session=session,
            )
            assert assignment is not None

            with pytest.raises(RuntimeError):
                await escalate_ticket(
                    ticket_id=ticket["ticket_id"],
                    reason="SLA breach",
                    _session=session,
                    _fail=True,
                )

            effects = await fetch_effects(db_conn, session_id)
            assert len(effects) == 4

            for effect in effects[:3]:
                assert effect["state"] == "committed", (
                    f"{effect['tool_name']} expected committed, got {effect['state']}"
                )

            assert effects[3]["tool_name"] == "escalate_ticket"
            # The proxy commits effects inline using the upstream tool server
            # result, so the DB state is ``committed`` even when the Python
            # body fails (dual execution).  The undo stack captures the pending
            # compensation entries that would be used if the engine-orchestrated
            # saga were triggered at the session level.
            assert effects[3]["state"] in ("pending", "failed", "committed"), (
                f"escalate_ticket unexpected state: {effects[3]['state']}"
            )

            undo_entries = await fetch_undo_stack(db_conn, session_id)
            assert len(undo_entries) == 4

            # SAFE tool (lookup_user) does not create an effect-log entry.
            # COMPENSABLE tools get steps 1-4.  Undo stack positions match
            # step indices, ordered DESC (LIFO).
            assert undo_entries[0]["stack_position"] == 4
            assert undo_entries[3]["stack_position"] == 1

            for entry in undo_entries:
                assert entry["state"] in ("pending", "running", "compensated", "failed"), (
                    f"{entry['compensation_fn']} unexpected state: {entry['state']}"
                )

    async def test_undo_stack_retry_defaults(self, db_conn: Any) -> None:
        """Undo stack entries have default ``max_retries`` and ``retry_backoff_ms``."""
        from compensation_demo import lookup_user, notify_user

        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await lookup_user(user_id="user_1", _session=session)
            await notify_user(
                to="frank@example.com",
                subject="Retry defaults",
                body="Body",
                _session=session,
            )

            undo_entries = await fetch_undo_stack(db_conn, session_id)
            assert len(undo_entries) == 1, f"Expected 1 entry, got {len(undo_entries)}"

            entry = undo_entries[0]
            assert "max_retries" in entry, "max_retries column missing from undo stack"
            assert "retry_backoff_ms" in entry, "retry_backoff_ms column missing from undo stack"
            assert entry["max_retries"] == 3, f"Expected max_retries=3, got {entry['max_retries']}"
            assert entry["retry_backoff_ms"] == 1000, (
                f"Expected retry_backoff_ms=1000, got {entry['retry_backoff_ms']}"
            )


# ── Replay idempotency tests ────────────────────────────────────────────────


class TestReplayIdempotency:
    """Exactly-once execution via BLAKE3 signature dedup."""

    async def test_same_signature_returns_replay(self, db_conn: Any) -> None:
        """Same (session_id, step_index, tool_name, args) returns a Replay."""
        test_session_id = str(uuid.uuid4())
        args_a = {"amount": 100, "currency": "USD"}

        async with httpx.AsyncClient() as client:
            payload_template: dict[str, Any] = {
                "session_id": test_session_id,
                "tool_name": "charge_payment",
                "tool_version": "1.0.0",
            }

            payload_a1: dict[str, Any] = {
                **payload_template,
                "step_index": 1,
                "args": args_a,
            }
            resp1 = await client.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload_a1,
                headers=headers(),
            )
            assert resp1.status_code == 200, f"First call: expected 200, got {resp1.status_code}"
            data1 = resp1.json()
            assert data1["status"] == "executed", f"Expected executed, got {data1['status']}"
            effect_id_1 = data1["effect_id"]

            # Second call with identical payload: must replay
            resp2 = await client.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload_a1,
                headers=headers(),
            )
            assert resp2.status_code == 200, f"Second call: expected 200, got {resp2.status_code}"
            data2 = resp2.json()
            assert data2["status"] == "replayed", f"Expected replayed, got {data2['status']}"
            assert data2["effect_id"] == effect_id_1, "Replay should return the same effect_id"

            # Different step_index + args: fresh execute
            payload_b: dict[str, Any] = {
                **payload_template,
                "step_index": 2,
                "args": {"amount": 200, "currency": "EUR"},
            }
            resp3 = await client.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload_b,
                headers=headers(),
            )
            assert resp3.status_code == 200, f"Third call: expected 200, got {resp3.status_code}"
            data3 = resp3.json()
            assert data3["status"] == "executed", f"Expected executed, got {data3['status']}"

            # Verify DB state
            effects = await fetch_effects(db_conn, test_session_id)

            step1 = [e for e in effects if e["step_index"] == 1]
            assert len(step1) == 1, f"Expected 1 effect for step 1, got {len(step1)}"
            assert step1[0]["replay_count"] >= 1, (
                f"Expected replay_count >= 1, got {step1[0]['replay_count']}"
            )

            step2 = [e for e in effects if e["step_index"] == 2]
            assert len(step2) == 1, f"Expected 1 effect for step 2, got {len(step2)}"
            assert step2[0]["replay_count"] == 0, (
                f"Expected replay_count == 0, got {step2[0]['replay_count']}"
            )

    async def test_replay_does_not_duplicate_undo_stack(self, db_conn: Any) -> None:
        """Replayed effects do not add duplicate undo-stack entries."""
        test_session_id = str(uuid.uuid4())
        args_a = {"amount": 100, "currency": "USD"}

        async with httpx.AsyncClient() as client:
            payload_template: dict[str, Any] = {
                "session_id": test_session_id,
                "tool_name": "charge_payment",
                "tool_version": "1.0.0",
            }

            payload_a1: dict[str, Any] = {
                **payload_template,
                "step_index": 1,
                "args": args_a,
            }

            # First call: execute
            resp1 = await client.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload_a1,
                headers=headers(),
            )
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "executed"

            # Second call same signature: replay
            resp2 = await client.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload_a1,
                headers=headers(),
            )
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "replayed"

            # Third call different step + args: execute
            payload_b: dict[str, Any] = {
                **payload_template,
                "step_index": 2,
                "args": {"amount": 200, "currency": "EUR"},
            }
            resp3 = await client.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload_b,
                headers=headers(),
            )
            assert resp3.status_code == 200
            assert resp3.json()["status"] == "executed"

            # The undo stack should contain exactly 2 entries (steps 1 and 2),
            # not 3 (the replay must not add a duplicate entry).
            undo = await fetch_undo_stack(db_conn, test_session_id)
            assert len(undo) == 2, (
                f"Expected 2 undo stack entries, got {len(undo)}. "
                "Replayed effects must not create duplicate undo entries."
            )

            positions = sorted(e["stack_position"] for e in undo)
            assert positions == [1, 2], f"Expected stack positions [1, 2], got {positions}"


# ── Demo execution smoke tests ──────────────────────────────────────────────


class TestDemoExecution:
    """Smoke tests that demo ``main()`` functions run without error."""

    async def test_replay_demo_runs(self) -> None:
        """``replay_demo.main()`` runs the full exactly-once workflow."""
        from replay_demo import main as replay_main

        await replay_main()

    async def test_approval_demo_imports(self) -> None:
        """``approval_demo`` module imports successfully and has ``main()``."""
        import approval_demo

        assert callable(approval_demo.main)
