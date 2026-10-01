"""SDK-owned live-stack integration tests (require a running UndoLog stack).

Exercises the SDK surface against the full docker compose stack: proxy,
engine, PostgreSQL, and the mock tool server. This is the release-gate
suite: it runs in CI on release tags only and locally with
``pytest -m integration`` after ``docker compose up -d``.

Scope
-----

- Intercept / execute / commit lifecycle through ``@undolog_tool``
- Replay idempotency: the same call signature returns the same effect
  and the cached result, without re-executing the function body
- Approval lifecycle: AwaitingApprovalError, approve via the proxy API,
  reject halts the session, double resolve returns 409
- Compensation registration: failing a COMPENSABLE effect registers
  undo-stack entries for the session

Prerequisites
-------------

*   Stack running (``docker compose up -d postgres tool-server proxy engine``).
*   ``asyncpg`` installed (``pip install asyncpg``).
*   ``TEST_DATABASE_URL`` or ``DATABASE_URL`` pointing at PostgreSQL.

Usage
-----

::

    docker compose up -d postgres tool-server proxy engine
    pytest -m integration sdks/undolog-py/tests/test_live_stack.py -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio

import undolog_sdk.client
from undolog_sdk import (
    AwaitingApprovalError,
    CompensationDescriptor,
    ToolTier,
    UndoLogClient,
    run_with_session,
    undolog_tool,
)
from undolog_sdk.session import UndoLogSession

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
]


# ── Configuration helpers ───────────────────────────────────────────────────


def proxy_url() -> str:
    """Return the proxy base URL from the environment."""
    return os.environ.get("UNDOLOG_PROXY_URL", "http://localhost:8080")


def db_url() -> str:
    """Return the database URL from the environment."""
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/undolog"
        ),
    )


def org_id() -> str:
    """Return the org identifier matching the seeded proxy API key."""
    return os.environ.get("UNDOLOG_ORG_ID", "org_demo")


def api_key() -> str:
    """Return the proxy API key from the environment."""
    key = os.environ.get("UNDOLOG_API_KEY", "dev-key")
    assert key, "UNDOLOG_API_KEY must be set for live-stack tests"
    return key


def headers() -> dict[str, str]:
    """Build request headers for direct proxy API calls."""
    return {
        "X-UndoLog-Org-Id": org_id(),
        "X-Api-Key": api_key(),
        "Content-Type": "application/json",
    }


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _skip_if_stack_down() -> Any:
    """Skip every test in this module when the stack is not running."""
    try:
        async with httpx.AsyncClient() as probe:
            resp = await probe.get(f"{proxy_url()}/health", timeout=3.0)
        assert resp.status_code == 200
    except (httpx.RequestError, AssertionError):
        pytest.skip("UndoLog stack not running (docker compose up -d)")


@pytest.fixture(autouse=True)
def _isolate_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep each test's default-client state isolated from other tests."""
    monkeypatch.setattr(undolog_sdk.client, "_DEFAULT_CLIENT", None)


@pytest_asyncio.fixture
async def db_conn() -> Any:
    """Provide a per-test asyncpg connection, or None when asyncpg is absent."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(db_url())
    yield conn
    await conn.close()


# ── DB helpers ──────────────────────────────────────────────────────────────


async def fetch_effects(conn: Any, session_id: str) -> list[dict[str, Any]]:
    """Return effect rows for a session ordered by step index."""
    rows = await conn.fetch(
        """
        SELECT tool_name, step_index, state::text
        FROM undolog_effect_log
        WHERE session_id = $1::uuid
        ORDER BY step_index
        """,
        session_id,
    )
    return [dict(row) for row in rows]


async def fetch_session(conn: Any, session_id: str) -> dict[str, Any] | None:
    """Return the session row, or None when absent."""
    row = await conn.fetchrow(
        """
        SELECT state::text
        FROM undolog_sessions
        WHERE session_id = $1::uuid
        """,
        session_id,
    )
    return dict(row) if row else None


async def fetch_undo_stack(conn: Any, session_id: str) -> list[dict[str, Any]]:
    """Return undo-stack rows for a session ordered LIFO."""
    rows = await conn.fetch(
        """
        SELECT stack_position, state::text
        FROM undolog_undo_stack
        WHERE session_id = $1::uuid
        ORDER BY stack_position DESC
        """,
        session_id,
    )
    return [dict(row) for row in rows]


# ── Shared tools under test ─────────────────────────────────────────────────


def _build_tools(client: UndoLogClient) -> dict[str, Any]:
    """Build decorated tools bound to the given client."""

    @undolog_tool(
        tier=ToolTier.SAFE,
        client=client,
    )
    async def lookup_customer(customer_id: str) -> dict[str, str]:
        return {"customer_id": customer_id, "name": "Test Customer"}

    @undolog_tool(
        tier=ToolTier.COMPENSABLE,
        compensation=CompensationDescriptor.new("compensate_charge_payment"),
        client=client,
    )
    async def charge_payment(amount: float, currency: str = "USD") -> dict[str, Any]:
        return {"charged": amount, "currency": currency}

    @undolog_tool(
        tier=ToolTier.IRREVERSIBLE,
        client=client,
    )
    async def escalate_case(ticket_id: str, reason: str) -> dict[str, str]:
        return {"escalated": ticket_id, "reason": reason}

    return {
        "lookup_customer": lookup_customer,
        "charge_payment": charge_payment,
        "escalate_case": escalate_case,
    }


# ── Lifecycle tests ─────────────────────────────────────────────────────────


class TestExecuteCommitLifecycle:
    """Intercept leads to Execute, then the effect commits in the database."""

    async def test_safe_and_compensable_effects_commit(self, db_conn: Any) -> None:
        """SAFE bypasses the journal; COMPENSABLE commits with a row."""
        tools = _build_tools(UndoLogClient())
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            safe_result = await tools["lookup_customer"](
                customer_id="cust_1", _session=session
            )
            assert safe_result == {"customer_id": "cust_1", "name": "Test Customer"}

            charged = await tools["charge_payment"](amount=42.5, _session=session)
            assert charged == {"charged": 42.5, "currency": "USD"}

            effects = await fetch_effects(db_conn, session_id)
            # SAFE tools do not journal; the COMPENSABLE call commits one row.
            assert [e["tool_name"] for e in effects] == ["charge_payment"]
            assert effects[0]["state"] == "committed"


class TestReplayIdempotency:
    """The same call signature replays the cached result exactly once."""

    async def test_same_signature_replays_without_reexecution(
        self, db_conn: Any
    ) -> None:
        """An identical (session, step, args) call replays, not re-executes.

        The replay contract lives at the proxy boundary: a call with the
        same session, step index, tool, and args must return the cached
        result and the original effect id. The SDK decorator cannot
        produce this naturally (its step counter always increments), so
        the contract is exercised with the same wire payload a replaying
        client would send.
        """
        session_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "session_id": session_id,
            "tool_name": "charge_payment",
            "tool_version": "1.0.0",
            "step_index": 1,
            "args": {"amount": 10.0},
        }

        async with httpx.AsyncClient() as http:
            first = await http.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload,
                headers=headers(),
            )
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "executed"
            effect_id: str = first.json()["effect_id"]

            replay = await http.post(
                f"{proxy_url()}/mcp/tool_call",
                json=payload,
                headers=headers(),
            )
            assert replay.status_code == 200, replay.text
            replay_body = replay.json()
            assert replay_body["status"] == "replayed"
            assert replay_body["effect_id"] == effect_id, (
                "replay must return the same effect_id"
            )

        row = await db_conn.fetchrow(
            """
            SELECT replay_count
            FROM undolog_effect_log
            WHERE effect_id = $1::uuid
            """,
            effect_id,
        )
        assert row is not None, "replayed effect must exist in the journal"
        assert row["replay_count"] >= 1, "replay must increment replay_count"

    async def test_distinct_step_same_args_executes_fresh(self) -> None:
        """Same args at a new step index is a new execution, not a replay."""
        calls: list[float] = []
        client = UndoLogClient()

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("compensate_charge_payment"),
            client=client,
        )
        async def metered_charge(amount: float) -> dict[str, Any]:
            calls.append(amount)
            return {"charged": amount}

        async with UndoLogSession(org_id=org_id()) as session:
            await metered_charge(10.0, _session=session)
            await metered_charge(10.0, _session=session)

        assert calls == [10.0, 10.0]


class TestApprovalLifecycle:
    """IRREVERSIBLE tools raise, and the proxy API resolves approvals."""

    async def test_irreversible_raises_and_approve_commits(self, db_conn: Any) -> None:
        """AwaitingApprovalError carries the id; approving commits the effect."""
        tools = _build_tools(UndoLogClient())
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            approval_id: str | None = None
            with pytest.raises(AwaitingApprovalError) as excinfo:
                await tools["escalate_case"](
                    ticket_id="TKT-LIVE-1",
                    reason="live stack approval",
                    _session=session,
                )
            approval_id = excinfo.value.approval_id
            assert approval_id, "AwaitingApprovalError must carry approval_id"

            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{proxy_url()}/approvals/{approval_id}/approve",
                    json={"actor": "sdk_live_test", "note": "approved by SDK test"},
                    headers=headers(),
                )
            assert resp.status_code == 200, resp.text
            assert resp.json().get("execution") == "committed"

            effects = await fetch_effects(db_conn, session_id)
            escalates = [e for e in effects if e["tool_name"] == "escalate_case"]
            assert len(escalates) == 1
            assert escalates[0]["state"] == "committed"

    async def test_reject_halts_session(self, db_conn: Any) -> None:
        """Rejecting transitions the effect and halts the session."""
        tools = _build_tools(UndoLogClient())
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            with pytest.raises(AwaitingApprovalError) as excinfo:
                await tools["escalate_case"](
                    ticket_id="TKT-LIVE-2",
                    reason="live stack reject",
                    _session=session,
                )
            approval_id = excinfo.value.approval_id
            assert approval_id

            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{proxy_url()}/approvals/{approval_id}/reject",
                    json={"actor": "sdk_live_test", "note": "rejected by SDK test"},
                    headers=headers(),
                )
            assert resp.status_code == 200, resp.text
            assert resp.json().get("status") == "rejected"

            effects = await fetch_effects(db_conn, session_id)
            escalates = [e for e in effects if e["tool_name"] == "escalate_case"]
            assert escalates[0]["state"] == "rejected"

            sess = await fetch_session(db_conn, session_id)
            assert sess is not None
            assert sess["state"] == "halted"

    async def test_double_approve_returns_conflict(self) -> None:
        """A second approve on a resolved approval returns 409."""
        tools = _build_tools(UndoLogClient())
        async with UndoLogSession(org_id=org_id()) as session:
            with pytest.raises(AwaitingApprovalError) as excinfo:
                await tools["escalate_case"](
                    ticket_id="TKT-LIVE-3",
                    reason="live stack double approve",
                    _session=session,
                )
            approval_id = excinfo.value.approval_id
            assert approval_id

            async with httpx.AsyncClient() as http:
                first = await http.post(
                    f"{proxy_url()}/approvals/{approval_id}/approve",
                    json={"actor": "sdk_live_test"},
                    headers=headers(),
                )
                assert first.status_code == 200, first.text

                second = await http.post(
                    f"{proxy_url()}/approvals/{approval_id}/approve",
                    json={"actor": "sdk_live_test"},
                    headers=headers(),
                )
            assert second.status_code == 409, second.text
            assert "already resolved" in second.text.lower()


class TestCompensationRegistration:
    """COMPENSABLE tools register undo-stack entries for their session."""

    async def test_commit_registers_undo_entry(self, db_conn: Any) -> None:
        """A committed COMPENSABLE effect has a pre-registered undo entry."""
        tools = _build_tools(UndoLogClient())
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            await tools["charge_payment"](amount=7.0, _session=session)

            undo_entries = await fetch_undo_stack(db_conn, session_id)
            assert len(undo_entries) == 1
            assert undo_entries[0]["stack_position"] == 1

    async def test_body_failure_keeps_journal_and_undo_entry(
        self, db_conn: Any
    ) -> None:
        """A Python-side body failure keeps the journal and undo entry.

        The proxy commits the upstream execution inline before the SDK
        body runs, so the effect row reflects the upstream result even
        when the local body raises. The pre-registered undo entry is
        what keeps the step rollback-capable after the failure. The
        tool used here (``send_email``) is registered upstream, matching
        the dual-execution contract the proxy implements.
        """
        client = UndoLogClient()

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("compensate_send_email"),
            client=client,
        )
        async def send_email(to: str, subject: str, body: str) -> dict[str, str]:
            raise RuntimeError("local rendering failed")

        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)

            with pytest.raises(RuntimeError, match="local rendering failed"):
                await send_email(
                    to="rollback@example.com",
                    subject="Body failure test",
                    body="Body",
                    _session=session,
                )

            effects = await fetch_effects(db_conn, session_id)
            assert [e["tool_name"] for e in effects] == ["send_email"]
            assert effects[0]["state"] in ("committed", "failed", "pending")

            undo_entries = await fetch_undo_stack(db_conn, session_id)
            assert len(undo_entries) == 1
            assert undo_entries[0]["stack_position"] == 1


class TestContextVarIntegration:
    """``run_with_session`` drives the decorator without _session kwargs."""

    async def test_tools_run_under_context_session(self, db_conn: Any) -> None:
        """Tools resolve the session from the context var and journal effects."""
        tools = _build_tools(UndoLogClient())
        async with UndoLogSession(org_id=org_id()) as session:
            session_id = str(session.session_id)
            async with run_with_session(session):
                charged = await tools["charge_payment"](amount=3.5)
                assert charged == {"charged": 3.5, "currency": "USD"}

            effects = await fetch_effects(db_conn, session_id)
            assert [e["tool_name"] for e in effects] == ["charge_payment"]
            assert effects[0]["state"] == "committed"


class TestUniqueSessionIsolation:
    """Sessions do not observe each other's effects."""

    async def test_two_sessions_journal_independently(self, db_conn: Any) -> None:
        """Effects land under distinct session ids."""
        tools = _build_tools(UndoLogClient())
        session_a = UndoLogSession(org_id=org_id())
        session_b = UndoLogSession(org_id=org_id())
        assert str(session_a.session_id) != str(session_b.session_id)

        async with session_a as sa:
            await tools["charge_payment"](amount=1.0, _session=sa)
        async with session_b as sb:
            await tools["charge_payment"](amount=2.0, _session=sb)

        effects_a = await fetch_effects(db_conn, str(session_a.session_id))
        effects_b = await fetch_effects(db_conn, str(session_b.session_id))
        assert [e["tool_name"] for e in effects_a] == ["charge_payment"]
        assert [e["tool_name"] for e in effects_b] == ["charge_payment"]
        assert effects_a[0]["step_index"] == effects_b[0]["step_index"] == 1
