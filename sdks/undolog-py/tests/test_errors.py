"""Tests for the typed error hierarchy.

Verifies that all SDK errors inherit from UndoLogError, wrap httpx
exceptions correctly, and preserve context (URL, status code, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from undolog_sdk import (
    ERROR_CLASSES,
    ERROR_DOCS_BASE_URL,
    ApprovalNotFoundError,
    AuthenticationError,
    AwaitingApprovalError,
    CompensationError,
    ConfigurationError,
    ConnectionError,
    EffectLogConcurrencyError,
    EffectLogError,
    ErrorCodes,
    InvalidStateTransitionError,
    MissingSessionError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    SerializationError,
    ServerError,
    TimeoutError,
    ToolNotRegisteredError,
    ToolRegistrationError,
    UndoLogError,
    ValidationError,
    format_runbook_url,
)
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


class TestStructuredErrorTaxonomy:
    """Tests for the 13 structured error classes and taxonomy."""

    def test_all_thirteen_classes_in_taxonomy_tuple(self) -> None:
        expected = {
            AuthenticationError,
            AwaitingApprovalError,
            CompensationError,
            ConfigurationError,
            EffectLogConcurrencyError,
            EffectLogError,
            MissingSessionError,
            NotFoundError,
            PermissionError,
            RateLimitError,
            SerializationError,
            ServerError,
            TimeoutError,
        }
        assert set(ERROR_CLASSES) == expected
        assert len(ERROR_CLASSES) == 13

    def test_all_structured_errors_inherit_from_undo_log_error(self) -> None:
        for err_cls in ERROR_CLASSES:
            assert issubclass(err_cls, UndoLogError)

    def test_runtime_error_subclasses(self) -> None:
        assert issubclass(AwaitingApprovalError, RuntimeError)
        assert issubclass(MissingSessionError, RuntimeError)

    def test_error_instances_have_code_details_and_runbook(self) -> None:
        for err_cls in ERROR_CLASSES:
            instance = err_cls("test failure message")
            assert isinstance(instance.code, str)
            assert len(instance.code) > 0
            assert isinstance(instance.details, dict)
            assert isinstance(instance.runbook_url, str)
            assert instance.runbook_url.startswith(ERROR_DOCS_BASE_URL + "#")
            assert str(instance) == "test failure message"

    def test_authentication_error_attributes(self) -> None:
        err = AuthenticationError("invalid token", status_code=401)
        assert err.code == ErrorCodes.AUTHENTICATION_FAILED
        assert err.status_code == 401
        assert err.details["status_code"] == 401
        assert "authentication-failed" in err.runbook_url

    def test_awaiting_approval_error_attributes(self) -> None:
        err = AwaitingApprovalError(
            "approval needed",
            approval_id="appr-123",
            tool_name="delete_database",
            call_id="call-456",
        )
        assert err.code == ErrorCodes.AWAITING_APPROVAL
        assert err.approval_id == "appr-123"
        assert err.tool_name == "delete_database"
        assert err.call_id == "call-456"
        assert err.details["approval_id"] == "appr-123"
        assert err.details["tool_name"] == "delete_database"
        assert err.details["call_id"] == "call-456"
        assert "awaiting-approval" in err.runbook_url

    def test_compensation_error_attributes(self) -> None:
        err = CompensationError(
            "compensation failed",
            effect_id="eff-99",
            step_index=3,
        )
        assert err.code == ErrorCodes.COMPENSATION_FAILED
        assert err.effect_id == "eff-99"
        assert err.step_index == 3
        assert err.details["effect_id"] == "eff-99"
        assert err.details["step_index"] == 3
        assert "compensation-failed" in err.runbook_url

    def test_configuration_error_attributes(self) -> None:
        err = ConfigurationError(
            "missing endpoint",
            setting="UNDOLOG_API_URL",
        )
        assert err.code == ErrorCodes.CONFIGURATION_INVALID
        assert err.setting == "UNDOLOG_API_URL"
        assert err.details["setting"] == "UNDOLOG_API_URL"
        assert "configuration-invalid" in err.runbook_url

    def test_effect_log_concurrency_error_attributes(self) -> None:
        err = EffectLogConcurrencyError(
            "conflict detected",
            session_id="sess-100",
            step_index=5,
        )
        assert err.code == ErrorCodes.EFFECT_LOG_CONCURRENCY
        assert err.session_id == "sess-100"
        assert err.step_index == 5
        assert err.details["session_id"] == "sess-100"
        assert err.details["step_index"] == 5
        assert "effect-log-concurrency" in err.runbook_url

    def test_effect_log_error_attributes(self) -> None:
        err = EffectLogError(
            "journal append failed",
            effect_id="eff-200",
            session_id="sess-200",
        )
        assert err.code == ErrorCodes.EFFECT_LOG_FAILED
        assert err.effect_id == "eff-200"
        assert err.session_id == "sess-200"
        assert err.details["effect_id"] == "eff-200"
        assert err.details["session_id"] == "sess-200"
        assert "effect-log-failed" in err.runbook_url

    def test_missing_session_error_attributes(self) -> None:
        err = MissingSessionError("no active session found")
        assert err.code == ErrorCodes.MISSING_SESSION
        assert "missing-session" in err.runbook_url

    def test_not_found_error_attributes(self) -> None:
        err = NotFoundError(
            "session not found",
            resource_type="session",
            resource_id="sess-300",
        )
        assert err.code == ErrorCodes.NOT_FOUND
        assert err.resource_type == "session"
        assert err.resource_id == "sess-300"
        assert err.details["resource_type"] == "session"
        assert err.details["resource_id"] == "sess-300"
        assert "not-found" in err.runbook_url

    def test_permission_error_attributes(self) -> None:
        err = PermissionError(
            "insufficient permissions",
            required_scope="effects:write",
        )
        assert err.code == ErrorCodes.PERMISSION_DENIED
        assert err.required_scope == "effects:write"
        assert err.details["required_scope"] == "effects:write"
        assert "permission-denied" in err.runbook_url

    def test_rate_limit_error_attributes(self) -> None:
        err = RateLimitError(
            "too many requests",
            retry_after=1.5,
            limit=100,
            remaining=0,
        )
        assert err.code == ErrorCodes.RATE_LIMITED
        assert err.retry_after == 1.5
        assert err.limit == 100
        assert err.remaining == 0
        assert err.details["retry_after"] == 1.5
        assert err.details["limit"] == 100
        assert err.details["remaining"] == 0
        assert "rate-limited" in err.runbook_url

    def test_serialization_error_attributes(self) -> None:
        err = SerializationError(
            "cannot serialize payload",
            format="json",
        )
        assert err.code == ErrorCodes.SERIALIZATION_FAILED
        assert err.format == "json"
        assert err.details["format"] == "json"
        assert "serialization-failed" in err.runbook_url

    def test_server_error_attributes(self) -> None:
        err = ServerError(
            "internal engine error",
            status_code=503,
            url="http://localhost:8080/v1/effects",
        )
        assert err.code == ErrorCodes.SERVER_ERROR
        assert err.status_code == 503
        assert err.url == "http://localhost:8080/v1/effects"
        assert err.details["status_code"] == 503
        assert err.details["url"] == "http://localhost:8080/v1/effects"
        assert "server-error" in err.runbook_url

    def test_timeout_error_attributes(self) -> None:
        err = TimeoutError(
            "gateway timed out",
            timeout=5.0,
            url="http://localhost:8080/v1/effects",
        )
        assert err.code == ErrorCodes.TIMEOUT
        assert err.timeout == 5.0
        assert err.url == "http://localhost:8080/v1/effects"
        assert err.details["timeout"] == 5.0
        assert err.details["url"] == "http://localhost:8080/v1/effects"
        assert "timeout" in err.runbook_url

    def test_custom_details_and_runbook_override(self) -> None:
        custom_url = "https://custom.runbook.local/fix"
        err = UndoLogError(
            "custom failure",
            code="CUSTOM_FAILURE",
            details={"worker": "w-1", "attempt": 2},
            runbook_url=custom_url,
        )
        assert err.code == "CUSTOM_FAILURE"
        assert err.runbook_url == custom_url
        assert err.docs_url == custom_url
        assert err.details == {"worker": "w-1", "attempt": 2}

    def test_to_dict_serialization(self) -> None:
        err = CompensationError(
            "step rollback failed",
            effect_id="eff-42",
            step_index=2,
            details={"custom_meta": True},
        )
        data = err.to_dict()
        assert data["error"] == "COMPENSATION_FAILED"
        assert data["message"] == "step rollback failed"
        assert data["runbook_url"] == format_runbook_url("COMPENSATION_FAILED")
        assert data["details"]["effect_id"] == "eff-42"
        assert data["details"]["step_index"] == 2
        assert data["details"]["custom_meta"] is True


class TestErrorLogging:
    """Tests for formatting errors for logs and python logging handlers."""

    def test_format_for_log_output(self) -> None:
        err = AuthenticationError("token expired", status_code=401)
        formatted = err.format_for_log()
        assert "[AUTHENTICATION_FAILED] token expired" in formatted
        assert (
            "(Runbook: https://undolog.dev/docs/troubleshooting/errors#authentication-failed)"
            in formatted
        )
        assert "(Details: {'status_code': 401})" in formatted

    def test_standard_logging_handler_capture(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = logging.getLogger("undolog.test")
        err = RateLimitError("quota exhausted", retry_after=30.0)
        with caplog.at_level(logging.WARNING):
            logger.warning(err.format_for_log())

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "RATE_LIMITED" in record.message
        assert (
            "https://undolog.dev/docs/troubleshooting/errors#rate-limited"
            in record.message
        )


class TestRunbookUrls:
    """Tests runbook URL resolution and slug anchors."""

    def test_format_runbook_url_slugs(self) -> None:
        assert format_runbook_url("AUTHENTICATION_FAILED") == (
            "https://undolog.dev/docs/troubleshooting/errors#authentication-failed"
        )
        assert format_runbook_url("EFFECT_LOG_CONCURRENCY") == (
            "https://undolog.dev/docs/troubleshooting/errors#effect-log-concurrency"
        )
        assert format_runbook_url("RATE_LIMITED") == (
            "https://undolog.dev/docs/troubleshooting/errors#rate-limited"
        )


class TestOtherStandardErrors:
    """Tests that other existing standard SDK error classes remain intact."""

    def test_other_classes_subclass_undo_log_error(self) -> None:
        other_classes = (
            ApprovalNotFoundError,
            ConnectionError,
            InvalidStateTransitionError,
            ToolNotRegisteredError,
            ToolRegistrationError,
            ValidationError,
        )
        for cls in other_classes:
            assert issubclass(cls, UndoLogError)
            inst = cls("test")
            assert isinstance(inst.code, str)
            assert inst.runbook_url.startswith(ERROR_DOCS_BASE_URL)
