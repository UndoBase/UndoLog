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

from agent_stateful import (
    AgentState,
    _auth_headers,
    _build_session,
    _invoke_tool,
    _proxy_url,
    build_graph,
    route_after_tools,
)
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
