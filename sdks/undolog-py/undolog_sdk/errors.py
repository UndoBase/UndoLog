"""Typed error hierarchy for the UndoLog Python SDK.

All SDK-specific exceptions inherit from ``UndoLogError``, allowing
callers to catch every SDK error with a single ``except UndoLogError``.
Each subclass wraps the underlying ``httpx`` exception and preserves the
original cause via ``raise ... from exc``.
"""

from __future__ import annotations


class UndoLogError(Exception):
    """Base class for all UndoLog SDK errors.

    Catch this to handle any SDK-level failure without knowing the
    specific subclass.
    """


class ConnectionError(UndoLogError):
    """Raised when the SDK cannot reach the UndoLog proxy.

    Wraps ``httpx.ConnectError``, ``httpx.ConnectTimeout``, and other
    connection-level failures.
    """

    def __init__(self, message: str, *, url: str = "") -> None:
        self.url = url
        super().__init__(message)


class TimeoutError(UndoLogError):
    """Raised when a proxy request exceeds its timeout.

    Wraps ``httpx.TimeoutException``.
    """

    def __init__(self, message: str, *, url: str = "", timeout: float = 0) -> None:
        self.url = url
        self.timeout = timeout
        super().__init__(message)


class AuthenticationError(UndoLogError):
    """Raised when the proxy rejects the request due to invalid credentials.

    Wraps HTTP 401 and 403 responses.
    """

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        self.status_code = status_code
        super().__init__(message)


class ServerError(UndoLogError):
    """Raised when the proxy returns a 5xx response.

    Wraps HTTP 500+ responses.
    """

    def __init__(self, message: str, *, status_code: int = 0, url: str = "") -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(message)
