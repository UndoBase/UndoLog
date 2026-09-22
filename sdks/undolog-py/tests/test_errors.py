"""Tests for the typed error hierarchy.

Verifies that all SDK errors inherit from UndoLogError, wrap httpx
exceptions correctly, and preserve context (URL, status code, etc.).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from undolog_sdk import (
    AuthenticationError as TopAuthError,
)
from undolog_sdk import (
    ConnectionError as TopConnectionError,
)
from undolog_sdk import (
    ServerError as TopServerError,
)
from undolog_sdk import (
    TimeoutError as TopTimeoutError,
)
from undolog_sdk import (
    UndoLogError as TopUndoLogError,
)
from undolog_sdk.client import UndoLogClient
from undolog_sdk.errors import (
    AuthenticationError,
    ConnectionError,
    ServerError,
    TimeoutError,
    UndoLogError,
)


class TestUndoLogErrorHierarchy:
    """Tests for the error class hierarchy."""

    def test_undo_log_error_is_base_exception(self) -> None:
        assert issubclass(UndoLogError, Exception)

    def test_connection_error_inherits_undo_log_error(self) -> None:
        assert issubclass(ConnectionError, UndoLogError)

    def test_timeout_error_inherits_undo_log_error(self) -> None:
        assert issubclass(TimeoutError, UndoLogError)

    def test_authentication_error_inherits_undo_log_error(self) -> None:
        assert issubclass(AuthenticationError, UndoLogError)

    def test_server_error_inherits_undo_log_error(self) -> None:
        assert issubclass(ServerError, UndoLogError)

    def test_all_errors_catchable_as_undo_log_error(self) -> None:
        for exc_class in (
            ConnectionError,
            TimeoutError,
            AuthenticationError,
            ServerError,
        ):
            exc = exc_class("test")
            assert isinstance(exc, UndoLogError)


class TestErrorContext:
    """Tests that errors preserve context fields."""

    def test_connection_error_has_url(self) -> None:
        exc = ConnectionError("msg", url="http://localhost:8080")
        assert exc.url == "http://localhost:8080"
        assert str(exc) == "msg"

    def test_timeout_error_has_url(self) -> None:
        exc = TimeoutError("msg", url="http://localhost:8080")
        assert exc.url == "http://localhost:8080"

    def test_authentication_error_has_status_code(self) -> None:
        exc = AuthenticationError("msg", status_code=401)
        assert exc.status_code == 401

    def test_server_error_has_status_code_and_url(self) -> None:
        exc = ServerError("msg", status_code=500, url="http://localhost:8080")
        assert exc.status_code == 500
        assert exc.url == "http://localhost:8080"

    def test_errors_default_to_empty_context(self) -> None:
        conn_exc = ConnectionError("msg")
        assert conn_exc.url == ""

        timeout_exc = TimeoutError("msg")
        assert timeout_exc.url == ""

        auth_exc = AuthenticationError("msg")
        assert auth_exc.status_code == 0

        server_exc = ServerError("msg")
        assert server_exc.status_code == 0
        assert server_exc.url == ""


class TestExportsFromInit:
    """Tests that error classes are exported from __init__."""

    def test_undo_log_error_exported(self) -> None:
        assert TopUndoLogError is UndoLogError

    def test_connection_error_exported(self) -> None:
        assert TopConnectionError is ConnectionError

    def test_timeout_error_exported(self) -> None:
        assert TopTimeoutError is TimeoutError

    def test_authentication_error_exported(self) -> None:
        assert TopAuthError is AuthenticationError

    def test_server_error_exported(self) -> None:
        assert TopServerError is ServerError


class TestClientWrapsExceptions:
    """Tests that UndoLogClient wraps httpx exceptions in SDK errors."""

    def test_intercept_wraps_connection_error(self) -> None:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(ConnectionError, match="Cannot connect"):
            asyncio.run(
                client.intercept(
                    org_id="org",
                    session_id="sess",
                    tool_name="tool",
                    step_index=0,
                    args={},
                )
            )

    def test_intercept_wraps_timeout_error(self) -> None:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(TimeoutError, match="timed out"):
            asyncio.run(
                client.intercept(
                    org_id="org",
                    session_id="sess",
                    tool_name="tool",
                    step_index=0,
                    args={},
                )
            )

    def test_intercept_wraps_auth_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(AuthenticationError, match="401"):
            asyncio.run(
                client.intercept(
                    org_id="org",
                    session_id="sess",
                    tool_name="tool",
                    step_index=0,
                    args={},
                )
            )

    def test_intercept_wraps_server_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.request.url = "http://localhost:8080"
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(ServerError, match="500"):
            asyncio.run(
                client.intercept(
                    org_id="org",
                    session_id="sess",
                    tool_name="tool",
                    step_index=0,
                    args={},
                )
            )

    def test_commit_wraps_connection_error(self) -> None:
        mock_http = AsyncMock()
        mock_http.put = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(ConnectionError, match="Cannot connect"):
            asyncio.run(
                client.commit(
                    org_id="org",
                    session_id="sess",
                    effect_id="eff-1",
                    result={},
                )
            )

    def test_fail_wraps_timeout_error(self) -> None:
        mock_http = AsyncMock()
        mock_http.put = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(TimeoutError, match="timed out"):
            asyncio.run(
                client.fail(
                    org_id="org",
                    session_id="sess",
                    effect_id="eff-1",
                    error="test",
                )
            )

    def test_approve_wraps_auth_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(AuthenticationError, match="403"):
            asyncio.run(client.approve(org_id="org", approval_id="appr-1"))

    def test_reject_wraps_server_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.request.url = "http://localhost:8080"
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        client = UndoLogClient(http_client=mock_http)

        with pytest.raises(ServerError, match="502"):
            asyncio.run(client.reject(org_id="org", approval_id="appr-1"))
