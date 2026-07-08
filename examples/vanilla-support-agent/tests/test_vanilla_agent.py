"""Tests for the vanilla asyncio agent (no framework)."""

from __future__ import annotations

import asyncio
import json
import os
from unittest import mock

import pytest

from example_tools import lookup_customer, send_email
from undolog_sdk import AwaitingApprovalError
from undolog_sdk.session import UndoLogSession

from vanilla_agent import (
    _build_openai_tool_def,
    _execute_tool_call,
    _make_wrapper,
    _to_json_schema_type,
    main,
)


class TestJsonSchemaConversion:
    """_to_json_schema_type converts Python types to JSON schema."""

    def test_str_type(self) -> None:
        assert _to_json_schema_type(str) == {"type": "string"}

    def test_int_type(self) -> None:
        assert _to_json_schema_type(int) == {"type": "integer"}

    def test_list_type(self) -> None:
        result = _to_json_schema_type(list[str])
        assert result["type"] == "array"
        assert result["items"]["type"] == "string"


class TestOpenAIToolDef:
    """_build_openai_tool_def generates correct OpenAI tool definitions."""

    def test_lookup_customer_has_required_params(self) -> None:
        definition = _build_openai_tool_def("lookup_customer", lookup_customer)
        assert definition["type"] == "function"
        params = definition["function"]["parameters"]
        assert "customer_id" in params["properties"]
        assert "customer_id" in params["required"]
        assert "_session" not in params["properties"]

    def test_send_email_has_all_params(self) -> None:
        definition = _build_openai_tool_def("send_email", send_email)
        params = definition["function"]["parameters"]
        assert "to" in params["properties"]
        assert "subject" in params["properties"]
        assert "body" in params["properties"]
        assert len(params["required"]) == 3


class TestToolWrapper:
    """_make_wrapper injects session from context var."""

    @pytest.mark.asyncio
    async def test_wrapper_injects_session(self) -> None:
        async def raw_fn(x: str, _session=None) -> dict[str, object]:
            return {"x": x, "has_session": _session is not None}

        wrapped = _make_wrapper("test", raw_fn)
        from vanilla_agent import _current_session

        session = UndoLogSession(org_id="org_test")
        _current_session.set(session)
        result = await wrapped(x="hello")
        assert result["x"] == "hello"
        assert result["has_session"] is True

    @pytest.mark.asyncio
    async def test_wrapper_raises_without_session(self) -> None:
        async def raw_fn(x: str, _session=None) -> dict[str, object]:
            return {"x": x}

        wrapped = _make_wrapper("test", raw_fn)
        from vanilla_agent import _current_session

        _current_session.set(None)
        with pytest.raises(RuntimeError, match="outside an active"):
            await wrapped(x="hello")


class TestToolExecution:
    """_execute_tool_call handles all outcomes."""

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        tool_call = {
            "id": "call_1",
            "function": {"name": "nonexistent", "arguments": "{}"},
        }
        result = await _execute_tool_call(tool_call, {})
        content = json.loads(result["content"])
        assert "error" in content
        assert "Unknown tool" in content["error"]

    @pytest.mark.asyncio
    async def test_tool_failure_returns_error_message(self) -> None:
        async def failing_fn(**kwargs: object) -> object:
            msg = "something went wrong"
            raise ValueError(msg)

        registry = {"failing": failing_fn}
        tool_call = {
            "id": "call_2",
            "function": {"name": "failing", "arguments": "{}"},
        }
        result = await _execute_tool_call(tool_call, registry)
        content = json.loads(result["content"])
        assert "error" in content
        assert "failing" in content["error"]

    @pytest.mark.asyncio
    async def test_aae_returns_approval_info(self) -> None:
        async def irreversible_fn(**kwargs: object) -> object:
            raise AwaitingApprovalError(approval_id="ap_1", tool_name="irreversible")

        registry = {"irreversible": irreversible_fn}
        tool_call = {
            "id": "call_3",
            "function": {"name": "irreversible", "arguments": "{}"},
        }
        from vanilla_agent import _current_session

        session = UndoLogSession(org_id="org_test")
        _current_session.set(session)
        result = await _execute_tool_call(tool_call, registry)
        content = json.loads(result["content"])
        assert content["status"] == "requires_approval"
        assert content["approval_id"] == "ap_1"


class TestMain:
    """main() handles missing env vars gracefully."""

    def test_main_requires_api_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            asyncio.run(main())  # should not raise, just log error
