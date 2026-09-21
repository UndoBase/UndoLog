"""Structured logging for the UndoLog Python SDK.

All runtime output uses Python's ``logging`` module. The canonical logger
for SDK internals is ``logging.getLogger("undolog_sdk")``. Each module
also creates its own child logger via ``logging.getLogger(__name__)`` so
that log records carry the originating module name.
"""

from __future__ import annotations

import logging


def get_sdk_logger() -> logging.Logger:
    """Return the top-level ``undolog_sdk`` logger.

    Returns:
        The root SDK logger instance.
    """
    return logging.getLogger("undolog_sdk")
