"""Tests for the LangGraph auto-instrumentation helpers.

The SDK has no LangGraph dependency: every test drives the wrappers
through minimal fake graphs and tool objects that mimic the duck-typed
surface the wrappers rely on (``ainvoke``, ``.name`` and ``.coroutine``
attributes).

Covers:
    - ``wrap_langgraph`` creates a session per invocation
    - Session identity resumes from graph state across invocations
    - Step counter carries across invocations
    - Tools resolve the session from the context var inside nodes
    - ``AwaitingApprovalError`` becomes ``awaiting_approval`` state
      that survives a re-invocation round trip
    - ``wrap_tool`` wrapping rules (compensation required, no double
      wrap of an already instrumented tool)
    - ``wrap_tools`` container copying and tool rejection rules
    - Delegation: graph attributes proxy through, private names do not
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from undolog_sdk import (
    AwaitingApprovalError,
    CompensationDescriptor,
    ToolTier,
    UndoLogSession,
    undolog_tool,
)
from undolog_sdk.client import InterceptResponse
from undolog_sdk.context import get_current_session
from undolog_sdk.integrations import (
    WrappedGraph,
    wrap_langgraph,
    wrap_tool,
    wrap_tools,
)

# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeGraph:
    """Minimal stand-in for a compiled LangGraph app."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.invocations: list[dict[str, Any]] = []
        self._result = result if result is not None else {"reply": "ok"}

    async def ainvoke(
        self, input: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        self.invocations.append({"input": input, "config": config, **kwargs})
        session = get_current_session()
        return {**self._result, "node_saw_session": session is not None}


class FakeStructuredTool:
    """Mimics a pydantic ``StructuredTool``: async callable on ``coroutine``."""

    def __init__(self, name: str, fn: Any) -> None:
        self.name = name
        self.coroutine = fn


# ── wrap_langgraph: session lifecycle ───────────────────────────────────────


class TestSessionLifecycle:
    """``ainvoke`` runs inside a session and reports its identity."""

    async def test_session_created_per_invocation(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")

        out = await graph.ainvoke({"q": "hi"})

        assert out["reply"] == "ok"
        assert out["node_saw_session"] is True, "nodes must see the session"
        assert out["session_id"], "a session id must be attached"
        assert out["undolog_step_index"] == 0
        assert out["awaiting_approval"] is False
        assert out["approval_request"] is None

    async def test_stale_approval_state_is_cleared(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")

        out = await graph.ainvoke(
            {
                "q": "hi",
                "awaiting_approval": True,
                "approval_request": {"approval_id": "stale-1"},
            }
        )

        assert out["awaiting_approval"] is False
        assert out["approval_request"] is None

    async def test_non_dict_graph_output_is_returned_unchanged(self) -> None:
        class ListGraph:
            async def ainvoke(
                self, input: dict[str, Any], config: Any = None, **kwargs: Any
            ) -> list[str]:
                return ["chunk"]

        out = await wrap_langgraph(ListGraph(), org_id="org_test").ainvoke({"q": "hi"})

        assert out == ["chunk"]

    async def test_distinct_invocations_get_distinct_sessions(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")

        first = await graph.ainvoke({"q": "1"})
        second = await graph.ainvoke({"q": "2"})

        assert first["session_id"] != second["session_id"]

    async def test_session_id_resumes_from_input_state(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")
        first = await graph.ainvoke({"q": "hi"})

        resumed = await graph.ainvoke({"q": "again", "session_id": first["session_id"]})

        assert resumed["session_id"] == first["session_id"], (
            "a stable session_id in state must resume the same session"
        )

    async def test_step_index_carries_across_invocations(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")

        out = await graph.ainvoke({"q": "hi", "undolog_step_index": 5})

        assert out["undolog_step_index"] == 5, (
            "step progress from state must not reset to zero"
        )

    async def test_config_and_kwargs_forwarded_to_graph(self) -> None:
        graph = FakeGraph()

        await wrap_langgraph(graph, org_id="org_test").ainvoke(
            {"q": "hi"},
            config={"configurable": {"thread_id": "t1"}},
            stream_mode="updates",
        )

        assert graph.invocations[0]["config"] == {"configurable": {"thread_id": "t1"}}
        assert graph.invocations[0]["stream_mode"] == "updates"


class TestApprovalTranslation:
    """``AwaitingApprovalError`` becomes state, not an escaping error."""

    async def test_irreversible_tool_raises_translates_to_state(self) -> None:
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-1")
        )

        @undolog_tool(
            tier=ToolTier.IRREVERSIBLE,
            client=client,
        )
        async def escalate_case(ticket_id: str) -> dict[str, str]:
            raise AssertionError("body must not execute on AwaitingApproval")

        async def node(state: dict[str, Any]) -> dict[str, Any]:
            # The context var is set by the wrapper: no _session threading.
            await escalate_case(ticket_id="TKT-1")
            return {}

        class NodeGraph:
            async def ainvoke(
                self, input: dict[str, Any], config: Any = None, **kwargs: Any
            ) -> dict[str, Any]:
                await node(input)
                return {"done": True}

        out = await wrap_langgraph(NodeGraph(), org_id="org_test").ainvoke({"q": "hi"})

        assert out["awaiting_approval"] is True
        assert out["approval_request"]["tool_name"] == "escalate_case"
        assert out["approval_request"]["approval_id"] == "approval-1"
        assert out["approval_request"]["step_index"] == 1
        assert out["session_id"], "approval output must carry session id"
        assert out["q"] == "hi", "the input state must survive for the re-invoke"

    async def test_pending_state_round_trips_into_next_invocation(self) -> None:
        """Re-invoking with the returned state resumes and completes."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-7"),
            InterceptResponse(outcome="Execute", effect_id="eff-7"),
        )
        runs: list[str] = []

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def escalate_case(ticket_id: str) -> dict[str, str]:
            runs.append(ticket_id)
            return {"status": "escalated"}

        class NodeGraph:
            async def ainvoke(
                self, input: dict[str, Any], config: Any = None, **kwargs: Any
            ) -> dict[str, Any]:
                await escalate_case(ticket_id="TKT-9")
                return {"done": True}

        graph = wrap_langgraph(NodeGraph(), org_id="org_test")

        pending = await graph.ainvoke({"q": "hi"})
        assert pending["awaiting_approval"] is True
        assert runs == [], "the tool body must not run before approval"

        resolved = await graph.ainvoke(pending)

        assert resolved["awaiting_approval"] is False
        assert resolved["approval_request"] is None
        assert resolved["session_id"] == pending["session_id"]
        assert runs == ["TKT-9"]

    async def test_approval_error_bubbles_outside_wrapper(self) -> None:
        """Direct calls outside ``ainvoke`` still raise: translation is local."""
        client = _client(
            InterceptResponse(outcome="AwaitingApproval", approval_id="approval-1")
        )

        @undolog_tool(tier=ToolTier.IRREVERSIBLE, client=client)
        async def escalate_case(ticket_id: str) -> dict[str, str]:
            return {}

        session = UndoLogSession(org_id="org_test")
        async with session:
            with pytest.raises(AwaitingApprovalError):
                await escalate_case(ticket_id="TKT-2", _session=session)


class TestWrapTool:
    """Single-tool wrapping rules."""

    async def test_compensable_without_compensation_name_raises(self) -> None:
        """Silently skipping the wrap would leave a tool unprotected."""

        async def tool(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="compensation name"):
            wrap_tool(tool)

    async def test_compensable_with_compensation_name_wraps(self) -> None:
        async def tool(x: int) -> int:
            return x

        wrapped = wrap_tool(tool, compensation_name="undo_tool")

        assert wrapped is not tool
        assert wrapped.__name__ == "tool"

    async def test_irreversible_wraps_without_compensation(self) -> None:
        async def tool(x: int) -> int:
            return x

        assert wrap_tool(tool, tier=ToolTier.IRREVERSIBLE) is not tool

    async def test_already_wrapped_tool_is_returned_unchanged(self) -> None:
        """A second wrap would journal every call twice."""

        @undolog_tool(
            tier=ToolTier.COMPENSABLE,
            compensation=CompensationDescriptor.new("undo_tool"),
        )
        async def tool(x: int) -> int:
            return x

        assert wrap_tool(tool) is tool

    async def test_safe_wraps_without_compensation(self) -> None:
        async def tool(x: int) -> int:
            return x

        assert wrap_tool(tool, tier=ToolTier.SAFE) is not tool

    async def test_wrapped_tool_requires_session(self) -> None:
        async def tool(x: int) -> int:
            return x * 2

        wrapped = wrap_tool(tool, tier=ToolTier.SAFE)

        with pytest.raises(RuntimeError, match="requires a session"):
            await wrapped(1)


class TestWrapTools:
    """Tool-list wrapping: containers copied, originals untouched."""

    async def test_structured_tool_container_is_copied(self) -> None:
        async def body(x: int) -> int:
            return x

        tool = FakeStructuredTool("body_tool", body)
        original = tool.coroutine

        result = wrap_tools([tool], compensations={"body_tool": "undo_body"})

        assert result[0] is not tool, "container must be copied, not mutated"
        assert tool.coroutine is original, "original tool must keep its callable"
        assert result[0].coroutine is not original

    async def test_missing_compensation_raises(self) -> None:
        """A COMPENSABLE tool with no mapping fails the build."""

        async def body(x: int) -> int:
            return x

        tool = FakeStructuredTool("body_tool", body)

        with pytest.raises(ValueError, match="compensation name"):
            wrap_tools([tool])

    async def test_tier_override_skips_compensation_requirement(self) -> None:
        async def body(x: int) -> int:
            return x

        tool = FakeStructuredTool("body_tool", body)

        result = wrap_tools([tool], tiers={"body_tool": ToolTier.SAFE})

        assert result[0] is not tool
        assert result[0].coroutine is not tool.coroutine

    async def test_plain_function_is_wrapped(self) -> None:
        async def solo_tool(x: int) -> int:
            return x

        result = wrap_tools([solo_tool], compensations={"solo_tool": "undo_solo"})

        assert result[0] is not solo_tool
        assert result[0].__name__ == "solo_tool"

    async def test_client_is_passed_to_wrapped_tools(self) -> None:
        client = _client(InterceptResponse(outcome="Execute", effect_id="eff-1"))

        async def solo_tool(x: int) -> int:
            return x * 2

        [tool] = wrap_tools(
            [solo_tool],
            compensations={"solo_tool": "undo_solo"},
            client=client,
        )

        async with UndoLogSession(org_id="org_test") as session:
            result = await tool(3, _session=session)

        assert result == 6
        client.intercept.assert_awaited_once()

    async def test_uninstrumentable_object_raises(self) -> None:
        """An object UndoLog cannot wrap fails instead of slipping through."""

        marker = object()

        with pytest.raises(ValueError, match="cannot instrument"):
            wrap_tools([marker])

    async def test_named_tool_without_function_name_raises(self) -> None:
        """A tool can carry a name and still lack the journal's ``__name__``."""

        class NamedTool:
            name = "named_tool"

            async def __call__(self, x: int) -> int:
                return x * 2

        with pytest.raises(ValueError, match="cannot instrument"):
            wrap_tools([NamedTool()])

    async def test_instrumented_tool_is_not_wrapped_twice(self) -> None:
        @undolog_tool(tier=ToolTier.SAFE)
        async def solo_tool(x: int) -> int:
            return x

        result = wrap_tools([solo_tool])

        assert result[0] is solo_tool


class TestDelegation:
    """The facade proxies graph attributes but keeps private state."""

    async def test_public_attribute_delegates(self) -> None:
        inner = FakeGraph()
        graph = wrap_langgraph(inner, org_id="org_test")

        assert graph.invocations == inner.invocations

    async def test_wrapped_graph_exposes_wrapped_graph_class(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")

        assert isinstance(graph, WrappedGraph)
        assert graph.org_id == "org_test"

    async def test_missing_private_attribute_raises_attribute_error(self) -> None:
        graph = wrap_langgraph(FakeGraph(), org_id="org_test")

        with pytest.raises(AttributeError):
            graph._not_a_real_attribute


class TestWrapLanggraphOrgResolution:
    """``org_id`` resolution: explicit argument wins, then environment."""

    async def test_explicit_org_id_wins_over_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        graph = wrap_langgraph(FakeGraph(), org_id="org_explicit")

        assert graph.org_id == "org_explicit"

    async def test_environment_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNDOLOG_ORG_ID", "org_env")

        graph = wrap_langgraph(FakeGraph())

        assert graph.org_id == "org_env"

    async def test_default_when_environment_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UNDOLOG_ORG_ID", raising=False)

        graph = wrap_langgraph(FakeGraph())

        assert graph.org_id == "org_demo"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _client(*outcomes: InterceptResponse) -> AsyncMock:
    """Build an UndoLog client stub returning ``outcomes`` in call order.

    Args:
        outcomes: One ``InterceptResponse`` per expected ``intercept``
            call, consumed sequentially. An unexpected extra call
            fails the test.

    Returns:
        An ``AsyncMock`` accepted anywhere ``undolog_tool`` takes a
        client, following the stub convention in ``test_decorators``.
    """
    client = AsyncMock()
    client.intercept.side_effect = list(outcomes)
    return client
