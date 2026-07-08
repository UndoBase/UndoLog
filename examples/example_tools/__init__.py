"""Shared tool definitions for UndoLog framework examples.

This package provides the canonical set of UndoLog-decorated tools
(SAFE, COMPENSABLE, IRREVERSIBLE) used across all framework-specific
example agents.

Usage
-----
::

    from example_tools import get_tool_registry
    from example_tools.compensations import compensate_create_ticket
"""

from __future__ import annotations

from example_tools.tools import (
    create_ticket,
    escalate_case,
    get_tool_registry,
    lookup_customer,
    send_email,
)

__all__ = [
    "create_ticket",
    "escalate_case",
    "get_tool_registry",
    "lookup_customer",
    "send_email",
]
