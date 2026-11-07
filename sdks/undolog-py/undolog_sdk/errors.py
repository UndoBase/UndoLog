"""Typed error hierarchy for the UndoLog Python SDK.

All SDK-specific exceptions inherit from ``UndoLogError``, allowing
callers to catch every SDK error with a single ``except UndoLogError``.
Every exception carries a machine-readable ``code``, a descriptive
``message``, a structured ``details`` dictionary, and a ``runbook_url``
linking directly to troubleshooting documentation.
"""

from __future__ import annotations

import re
from typing import Any

ERROR_DOCS_BASE_URL = "https://undolog.dev/docs/troubleshooting/errors"


def format_runbook_url(code: str) -> str:
    """Construct the canonical troubleshooting runbook URL for an error code.

    Parameters:
        code: Machine-readable error code.

    Returns:
        Formatted URL linking to the error's section anchor.
    """
    if not code:
        return ERROR_DOCS_BASE_URL
    slug = code.strip().lower()
    slug = re.sub(r"[_\s]+", "-", slug)
    clean_slug = re.sub(r"[^a-z0-9-]", "", slug)
    return f"{ERROR_DOCS_BASE_URL}#{clean_slug}" if clean_slug else ERROR_DOCS_BASE_URL


format_error_docs_url = format_runbook_url
"""Alias for :func:`format_runbook_url` matching TypeScript SDK nomenclature."""


class ErrorCodes:
    """Standard UndoLog error code identifiers."""

    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    EFFECT_LOG_CONCURRENCY = "EFFECT_LOG_CONCURRENCY"
    EFFECT_LOG_FAILED = "EFFECT_LOG_FAILED"
    MISSING_SESSION = "MISSING_SESSION"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    SERIALIZATION_FAILED = "SERIALIZATION_FAILED"
    SERVER_ERROR = "SERVER_ERROR"
    TIMEOUT = "TIMEOUT"
    TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
    TOOL_REGISTRATION_FAILED = "TOOL_REGISTRATION_FAILED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class UndoLogError(Exception):
    """Base class for all UndoLog SDK errors.

    Catch this to handle any SDK-level failure without knowing the
    specific subclass.
    """

    def __init__(
        self,
        message: str = "",
        *,
        code: str = "UNDOLOG_ERROR",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        """Initialize UndoLogError.

        Parameters:
            message: Human-readable error description.
            code: Machine-readable error identifier.
            details: Contextual metadata dictionary.
            runbook_url: Direct documentation runbook link.
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.runbook_url = runbook_url or format_runbook_url(code)
        self.docs_url = self.runbook_url

    def format_for_log(self) -> str:
        """Format error for structured logging including code and runbook URL."""
        parts = [f"[{self.code}] {self.message}", f"(Runbook: {self.runbook_url})"]
        if self.details:
            parts.append(f"(Details: {self.details})")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize error to a JSON-compatible dictionary."""
        return {
            "name": self.__class__.__name__,
            "code": self.code,
            "error": self.code,
            "message": self.message,
            "details": dict(self.details),
            "runbook_url": self.runbook_url,
        }


class AuthenticationError(UndoLogError):
    """Raised when authentication or authorization fails."""

    def __init__(
        self,
        message: str = "Authentication failed.",
        *,
        status_code: int = 0,
        reason: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        merged_details = dict(details or {})
        if status_code:
            merged_details.setdefault("status_code", status_code)
        if reason:
            merged_details.setdefault("reason", reason)
        super().__init__(
            message,
            code=ErrorCodes.AUTHENTICATION_FAILED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class AwaitingApprovalError(UndoLogError, RuntimeError):
    """Raised when an Irreversible tool call requires human approval."""

    def __init__(
        self,
        message_or_approval_id: str = "",
        tool_name: str = "",
        step_index: int = 0,
        message: str | None = None,
        *,
        approval_id: str = "",
        call_id: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        if approval_id:
            resolved_approval_id = approval_id
            resolved_message = message or message_or_approval_id
        elif message is not None or tool_name or step_index:
            resolved_approval_id = message_or_approval_id
            resolved_message = message or ""
        else:
            resolved_approval_id = ""
            resolved_message = message_or_approval_id

        self.approval_id = resolved_approval_id
        self.tool_name = tool_name
        self.step_index = step_index
        self.call_id = call_id
        msg = resolved_message or (
            f"Tool '{tool_name}' (step {step_index}) requires approval. "
            f"approval_id={resolved_approval_id}"
        )
        merged_details = dict(details or {})
        if resolved_approval_id:
            merged_details.setdefault("approval_id", resolved_approval_id)
        if tool_name:
            merged_details.setdefault("tool_name", tool_name)
        if step_index:
            merged_details.setdefault("step_index", step_index)
        if call_id:
            merged_details.setdefault("call_id", call_id)
        super().__init__(
            msg,
            code=ErrorCodes.AWAITING_APPROVAL,
            details=merged_details,
            runbook_url=runbook_url,
        )


class CompensationError(UndoLogError):
    """Raised when a compensation function fails after retries."""

    def __init__(
        self,
        message: str = "Compensation failed.",
        *,
        effect_id: str = "",
        step_index: int = 0,
        fn_name: str = "",
        retries: int = 0,
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.effect_id = effect_id
        self.step_index = step_index
        self.fn_name = fn_name
        self.retries = retries
        merged_details = dict(details or {})
        if effect_id:
            merged_details.setdefault("effect_id", effect_id)
        if step_index:
            merged_details.setdefault("step_index", step_index)
        if fn_name:
            merged_details.setdefault("fn_name", fn_name)
        if retries:
            merged_details.setdefault("retries", retries)
        super().__init__(
            message,
            code=ErrorCodes.COMPENSATION_FAILED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ConfigurationError(UndoLogError):
    """Raised when SDK configuration is invalid or incomplete."""

    def __init__(
        self,
        message: str = "Invalid configuration.",
        *,
        key: str = "",
        setting: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        resolved_key = key or setting
        resolved_setting = setting or key
        self.key = resolved_key
        self.setting = resolved_setting
        merged_details = dict(details or {})
        if resolved_key:
            merged_details.setdefault("key", resolved_key)
        if resolved_setting:
            merged_details.setdefault("setting", resolved_setting)
        super().__init__(
            message,
            code=ErrorCodes.CONFIGURATION_INVALID,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ConnectionError(UndoLogError):
    """Raised when the SDK cannot reach the UndoLog proxy."""

    def __init__(
        self,
        message: str = "Cannot connect to UndoLog proxy.",
        *,
        url: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.url = url
        merged_details = dict(details or {})
        if url:
            merged_details.setdefault("url", url)
        super().__init__(
            message,
            code=ErrorCodes.CONNECTION_FAILED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class EffectLogError(UndoLogError):
    """Raised when an effect log persistence operation fails."""

    def __init__(
        self,
        message: str = "Effect log operation failed.",
        *,
        effect_id: str = "",
        session_id: str = "",
        operation: str = "",
        code: str = ErrorCodes.EFFECT_LOG_FAILED,
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.effect_id = effect_id
        self.session_id = session_id
        self.operation = operation
        merged_details = dict(details or {})
        if effect_id:
            merged_details.setdefault("effect_id", effect_id)
        if session_id:
            merged_details.setdefault("session_id", session_id)
        if operation:
            merged_details.setdefault("operation", operation)
        super().__init__(
            message,
            code=code,
            details=merged_details,
            runbook_url=runbook_url,
        )


class EffectLogConcurrencyError(EffectLogError):
    """Raised when concurrent modification is detected on the effect log."""

    def __init__(
        self,
        message: str = "Effect log concurrency conflict detected.",
        *,
        session_id: str = "",
        step_index: int = 0,
        operation: str = "",
        expected_version: int = 0,
        actual_version: int = 0,
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.step_index = step_index
        self.expected_version = expected_version
        self.actual_version = actual_version
        merged_details = dict(details or {})
        if session_id:
            merged_details.setdefault("session_id", session_id)
        if step_index:
            merged_details.setdefault("step_index", step_index)
        if expected_version:
            merged_details.setdefault("expected_version", expected_version)
        if actual_version:
            merged_details.setdefault("actual_version", actual_version)
        super().__init__(
            message,
            session_id=session_id,
            operation=operation,
            code=ErrorCodes.EFFECT_LOG_CONCURRENCY,
            details=merged_details,
            runbook_url=runbook_url,
        )


class MissingSessionError(UndoLogError, RuntimeError):
    """Raised when an instrumented tool runs without an active session."""

    def __init__(
        self,
        message: str = "Tool executed without an active session context.",
        *,
        tool_name: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        merged_details = dict(details or {})
        if tool_name:
            merged_details.setdefault("tool_name", tool_name)
        super().__init__(
            message,
            code=ErrorCodes.MISSING_SESSION,
            details=merged_details,
            runbook_url=runbook_url,
        )


class NotFoundError(UndoLogError):
    """Raised when a requested resource is not found."""

    def __init__(
        self,
        message: str = "Resource not found.",
        *,
        resource_type: str = "",
        resource_id: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        merged_details = dict(details or {})
        if resource_type:
            merged_details.setdefault("resource_type", resource_type)
        if resource_id:
            merged_details.setdefault("resource_id", resource_id)
        super().__init__(
            message,
            code=ErrorCodes.NOT_FOUND,
            details=merged_details,
            runbook_url=runbook_url,
        )


class PermissionError(UndoLogError):
    """Raised when permissions are insufficient for the requested operation."""

    def __init__(
        self,
        message: str = "Permission denied.",
        *,
        permission: str = "",
        required_scope: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        resolved_permission = permission or required_scope
        resolved_scope = required_scope or permission
        self.permission = resolved_permission
        self.required_scope = resolved_scope
        merged_details = dict(details or {})
        if resolved_permission:
            merged_details.setdefault("permission", resolved_permission)
        if resolved_scope:
            merged_details.setdefault("required_scope", resolved_scope)
        super().__init__(
            message,
            code=ErrorCodes.PERMISSION_DENIED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class RateLimitError(UndoLogError):
    """Raised when a rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit exceeded (HTTP 429)",
        *,
        retry_after: float = 0.0,
        retry_after_seconds: float = 0.0,
        retry_after_ms: int = 1000,
        limit: int | None = None,
        remaining: int | None = None,
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        secs = retry_after or retry_after_seconds
        if retry_after_ms and not secs:
            secs = retry_after_ms / 1000.0
        elif secs and retry_after_ms == 1000 and (retry_after or retry_after_seconds):
            retry_after_ms = int(secs * 1000)
        self.retry_after = secs
        self.retry_after_seconds = secs
        self.retry_after_ms = retry_after_ms
        self.limit = limit if limit is not None else 0
        self.remaining = remaining if remaining is not None else 0
        merged_details = dict(details or {})
        if secs:
            merged_details.setdefault("retry_after", secs)
            merged_details.setdefault("retry_after_seconds", secs)
        if retry_after_ms:
            merged_details.setdefault("retry_after_ms", retry_after_ms)
        if limit is not None:
            merged_details.setdefault("limit", limit)
        if remaining is not None:
            merged_details.setdefault("remaining", remaining)
        super().__init__(
            message,
            code=ErrorCodes.RATE_LIMITED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class SerializationError(UndoLogError):
    """Raised when serialization or deserialization of an entry fails."""

    def __init__(
        self,
        message: str = "Serialization failed.",
        *,
        format: str = "json",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.format = format
        merged_details = dict(details or {})
        if format:
            merged_details.setdefault("format", format)
        super().__init__(
            message,
            code=ErrorCodes.SERIALIZATION_FAILED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ServerError(UndoLogError):
    """Raised when the UndoLog proxy returns a 5xx response."""

    def __init__(
        self,
        message: str = "Server error.",
        *,
        status_code: int = 0,
        url: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        merged_details = dict(details or {})
        if status_code:
            merged_details.setdefault("status_code", status_code)
        if url:
            merged_details.setdefault("url", url)
        super().__init__(
            message,
            code=ErrorCodes.SERVER_ERROR,
            details=merged_details,
            runbook_url=runbook_url,
        )


class TimeoutError(UndoLogError):
    """Raised when an operation or proxy request exceeds its deadline."""

    def __init__(
        self,
        message: str = "Operation timed out.",
        *,
        url: str = "",
        timeout: float = 0.0,
        operation: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.operation = operation
        merged_details = dict(details or {})
        if url:
            merged_details.setdefault("url", url)
        if timeout:
            merged_details.setdefault("timeout", timeout)
        if operation:
            merged_details.setdefault("operation", operation)
        super().__init__(
            message,
            code=ErrorCodes.TIMEOUT,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ToolNotRegisteredError(UndoLogError):
    """Raised when attempting to execute a tool that is not registered."""

    def __init__(
        self,
        message: str = "Tool is not registered.",
        *,
        tool_name: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        merged_details = dict(details or {})
        if tool_name:
            merged_details.setdefault("tool_name", tool_name)
        super().__init__(
            message,
            code=ErrorCodes.TOOL_NOT_REGISTERED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ToolRegistrationError(UndoLogError):
    """Raised when tool registration fails."""

    def __init__(
        self,
        message: str = "Tool registration failed.",
        *,
        tool_name: str = "",
        reason: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.reason = reason
        merged_details = dict(details or {})
        if tool_name:
            merged_details.setdefault("tool_name", tool_name)
        if reason:
            merged_details.setdefault("reason", reason)
        super().__init__(
            message,
            code=ErrorCodes.TOOL_REGISTRATION_FAILED,
            details=merged_details,
            runbook_url=runbook_url,
        )


class InvalidStateTransitionError(UndoLogError):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        message: str = "Invalid state transition.",
        *,
        current_state: str = "",
        target_state: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        merged_details = dict(details or {})
        if current_state:
            merged_details.setdefault("current_state", current_state)
        if target_state:
            merged_details.setdefault("target_state", target_state)
        super().__init__(
            message,
            code=ErrorCodes.INVALID_STATE_TRANSITION,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ApprovalNotFoundError(UndoLogError):
    """Raised when the requested approval request is not found."""

    def __init__(
        self,
        message: str = "Approval not found.",
        *,
        approval_id: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.approval_id = approval_id
        merged_details = dict(details or {})
        if approval_id:
            merged_details.setdefault("approval_id", approval_id)
        super().__init__(
            message,
            code=ErrorCodes.APPROVAL_NOT_FOUND,
            details=merged_details,
            runbook_url=runbook_url,
        )


class ValidationError(UndoLogError):
    """Raised when user-supplied input validation fails."""

    def __init__(
        self,
        message: str = "Validation failed.",
        *,
        field: str = "",
        constraint: str = "",
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
    ) -> None:
        self.field = field
        self.constraint = constraint
        merged_details = dict(details or {})
        if field:
            merged_details.setdefault("field", field)
        if constraint:
            merged_details.setdefault("constraint", constraint)
        super().__init__(
            message,
            code=ErrorCodes.VALIDATION_FAILED,
            details=merged_details,
            runbook_url=runbook_url,
        )


ERROR_CLASSES: tuple[type[UndoLogError], ...] = (
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
)
