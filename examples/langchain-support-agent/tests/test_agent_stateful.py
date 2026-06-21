"""Tests for the stateful LangGraph agent with approval branching.

Verifies
--------
1. Module imports and graph construction.
2. Conditional routing logic (halted → end, pending_approval → end,
   normal tool_calls → continue).
3. Session reconstruction from graph state.
4. Helper functions produce correct values.
5. Tool invocation handles errors gracefully.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from agent_stateful import (
    AgentState,
    _auth_headers,
    _build_session,
    _invoke_tool,
    _proxy_url,
    build_graph,
    execute_tools,
    route_after_tools,
)
from undolog_sdk import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession


class TestGraphConstruction:
    """Graph compiles with correct structure."""

    def test_build_graph_returns_compiled_graph(self) -> None:
        graph = build_graph()
        assert graph is not None
        assert hasattr(graph, "ainvoke")

    def test_graph_has_required_nodes(self) -> None:
        graph = build_graph()
        nodes = list(graph.nodes.keys())
        assert "model" in nodes
        assert "tools" in nodes

    def test_graph_has_conditional_edges(self) -> None:
        graph = build_graph()
        schema = graph.get_graph().nodes
        tools_node = schema.get("tools")
        assert tools_node is not None, "tools node must exist"

    def test_graph_has_checkpointer(self) -> None:
        graph = build_graph()
        assert graph.checkpointer is not None
        assert isinstance(graph.checkpointer, MemorySaver)


class TestRouting:
    """route_after_tools returns correct targets for each state."""

    def test_routes_to_end_when_halted(self) -> None:
        state: AgentState = {
            "messages": [],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": True,
            "pending_approval": None,
        }
        assert route_after_tools(state) == "end"

    def test_routes_to_end_when_pending_approval(self) -> None:
        state: AgentState = {
            "messages": [],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": {"approval_id": "ap_1", "tool_name": "x"},
        }
        assert route_after_tools(state) == "end"

    def test_routes_to_continue_when_tool_calls_exist(self) -> None:
        msg = AIMessage(content="", tool_calls=[{"name": "test", "args": {}, "id": "1"}])
        state: AgentState = {
            "messages": [msg],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": None,
        }
        assert route_after_tools(state) == "continue"

    def test_routes_to_end_when_no_messages(self) -> None:
        state: AgentState = {
            "messages": [],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": None,
        }
        assert route_after_tools(state) == "end"

    def test_routes_to_end_when_final_response(self) -> None:
        msg = AIMessage(content="Here is the answer")
        state: AgentState = {
            "messages": [msg],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": None,
        }
        assert route_after_tools(state) == "end"


class TestBuildSession:
    """_build_session reconstructs sessions from graph state."""

    def test_build_with_default_org(self) -> None:
        state: AgentState = {
            "messages": [],
            "org_id": "org_test",
            "session_id": "sess_abc",
            "halted": False,
            "pending_approval": None,
        }
        session = _build_session(state)
        assert isinstance(session, UndoLogSession)
        assert session.org_id == "org_test"

    def test_build_preserves_session_id(self) -> None:
        state: AgentState = {
            "messages": [],
            "org_id": "org_test",
            "session_id": "sess_abc",
            "halted": False,
            "pending_approval": None,
        }
        session = _build_session(state)
        assert session.session_id == "sess_abc"

    def test_build_resets_step_index(self) -> None:
        state: AgentState = {
            "messages": [],
            "org_id": "org_test",
            "session_id": "sess_abc",
            "halted": False,
            "pending_approval": None,
        }
        session = _build_session(state)
        assert session._step_index == 0  # noqa: SLF001


class TestHelpers:
    """Helper functions produce correct values."""

    def test_default_proxy_url(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            assert _proxy_url() == "http://localhost:8080"

    def test_custom_proxy_url(self) -> None:
        with mock.patch.dict(os.environ, {"UNDOLOG_PROXY_URL": "http://proxy:9090"}):
            assert _proxy_url() == "http://proxy:9090"

    def test_auth_headers_without_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            headers = _auth_headers()
            assert "Content-Type" in headers
            assert "X-Api-Key" not in headers

    def test_auth_headers_with_api_key(self) -> None:
        with mock.patch.dict(os.environ, {"UNDOLOG_API_KEY": "sk-test"}):
            headers = _auth_headers()
            assert headers["X-Api-Key"] == "sk-test"

    def test_import_main(self) -> None:
        from agent_stateful import main

        assert callable(main)


class TestToolInvocation:
    """_invoke_tool handles errors gracefully."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_message(self) -> None:
        session = UndoLogSession(org_id="org_test")
        tool_call = {"name": "nonexistent_tool", "args": {}, "id": "call_1"}
        result = await _invoke_tool(tool_call, session)
        assert isinstance(result, ToolMessage)
        assert "Unknown tool" in result.content
        assert result.tool_call_id == "call_1"


class TestInterruptLifecycle:
    """Interrupt / approve / reject lifecycle through execute_tools."""

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_empty(self) -> None:
        msg = AIMessage(content="Hello")
        state: AgentState = {
            "messages": [msg],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": None,
        }
        result = await execute_tools(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_catches_aae_and_sets_pending_approval(self) -> None:
        tool_call = {"name": "lookup_customer", "args": {"customer_id": "c1"}, "id": "call_1"}
        msg = AIMessage(content="", tool_calls=[tool_call])
        state: AgentState = {
            "messages": [msg],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": None,
        }
        aae = AwaitingApprovalError(approval_id="ap_1", tool_name="lookup_customer")

        with (
            mock.patch("agent_stateful._invoke_tool", side_effect=aae),
            mock.patch("agent_stateful.interrupt", return_value=None),
        ):
            result = await execute_tools(state)

        assert result["pending_approval"]["approval_id"] == "ap_1"
        assert result["pending_approval"]["tool_name"] == "lookup_customer"
        assert result["pending_approval"]["tool_call"]["name"] == "lookup_customer"

    @pytest.mark.asyncio
    async def test_approve_resumes_and_calls_api(self) -> None:
        tool_call = {"name": "lookup_customer", "args": {"customer_id": "c1"}, "id": "call_1"}
        msg = AIMessage(content="", tool_calls=[tool_call])
        state: AgentState = {
            "messages": [msg],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": {
                "approval_id": "ap_1",
                "tool_name": "lookup_customer",
                "tool_call": tool_call,
            },
        }
        mock_result = ToolMessage(content="ok", tool_call_id="call_1")

        with (
            mock.patch("agent_stateful.interrupt", return_value="approve"),
            mock.patch("agent_stateful._approve_via_api", return_value={}),
            mock.patch("agent_stateful._invoke_tool", return_value=mock_result),
        ):
            result = await execute_tools(state)

        assert result["messages"][0].tool_call_id == "call_1"
        assert result["pending_approval"] is None

    @pytest.mark.asyncio
    async def test_reject_sets_halted(self) -> None:
        tool_call = {"name": "lookup_customer", "args": {"customer_id": "c1"}, "id": "call_1"}
        msg = AIMessage(content="", tool_calls=[tool_call])
        state: AgentState = {
            "messages": [msg],
            "org_id": "org_test",
            "session_id": "sess_1",
            "halted": False,
            "pending_approval": {
                "approval_id": "ap_1",
                "tool_name": "lookup_customer",
                "tool_call": tool_call,
            },
        }

        with mock.patch("agent_stateful.interrupt", return_value="reject"):
            result = await execute_tools(state)

        assert result["halted"] is True
        assert result["pending_approval"] is None
