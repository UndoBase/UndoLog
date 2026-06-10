"""
Tests for the LangChain Support Agent example.

Verifies
--------
1. Tools are decorated with the correct UndoLog tier.
2. Compensation descriptors are configured properly.
3. Compensation handlers produce expected results.
4. Tool registry exposes all four tools with correct metadata.
"""

from __future__ import annotations

import inspect


from compensations import compensate_create_ticket, compensate_send_email
from tools import (
    create_ticket,
    escalate_case,
    get_tool_registry,
    lookup_customer,
    send_email,
)
from undolog_sdk import CompensationDescriptor, ToolTier


class TestToolTiers:
    """Each tool must have the correct UndoLog tier."""

    def test_lookup_customer_is_safe(self) -> None:
        assert getattr(lookup_customer, "_undolog_tier", None) is ToolTier.SAFE

    def test_send_email_is_compensable(self) -> None:
        assert getattr(send_email, "_undolog_tier", None) is ToolTier.COMPENSABLE

    def test_create_ticket_is_compensable(self) -> None:
        assert getattr(create_ticket, "_undolog_tier", None) is ToolTier.COMPENSABLE

    def test_escalate_case_is_irreversible(self) -> None:
        assert getattr(escalate_case, "_undolog_tier", None) is ToolTier.IRREVERSIBLE


class TestCompensationDescriptors:
    """COMPENSABLE tools must have a compensation descriptor."""

    def test_send_email_has_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(send_email, "_undolog_compensation", None)
        assert comp is not None
        assert comp.fn_name == "compensate_send_email"
        assert "to" in comp.args

    def test_create_ticket_has_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(create_ticket, "_undolog_compensation", None)
        assert comp is not None
        assert comp.fn_name == "compensate_create_ticket"
        assert "ticket_id" in comp.args

    def test_safe_tool_has_no_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(
            lookup_customer, "_undolog_compensation", None
        )
        assert comp is None

    def test_irreversible_tool_has_no_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(escalate_case, "_undolog_compensation", None)
        assert comp is None


class TestCompensationHandlers:
    """Verify compensation functions produce expected results."""

    def test_compensate_send_email(self) -> None:
        result = compensate_send_email({"to": "test@example.com", "subject": "Welcome"})
        assert result["status"] == "correction_sent"
        assert result["to"] == "test@example.com"

    def test_compensate_create_ticket(self) -> None:
        result = compensate_create_ticket({"ticket_id": "TKT-12345678"})
        assert result["status"] == "ticket_closed"
        assert result["ticket_id"] == "TKT-12345678"

    def test_compensate_email_missing_args(self) -> None:
        result = compensate_send_email({})
        assert result["status"] == "correction_sent"
        assert result["to"] == "unknown"


class TestToolRegistry:
    """Tool registry must expose all tools."""

    def test_registry_has_all_tools(self) -> None:
        registry = get_tool_registry()
        assert set(registry.keys()) == {
            "lookup_customer",
            "send_email",
            "create_ticket",
            "escalate_case",
        }

    def test_registry_tools_are_async(self) -> None:
        for name, tool in get_tool_registry().items():
            assert inspect.iscoroutinefunction(tool), f"{name} is not async"

    def test_each_tool_has_name(self) -> None:
        for name, tool in get_tool_registry().items():
            stored: str | None = getattr(tool, "_undolog_tool_name", None)
            assert stored == name, f"{name} has wrong tool name: {stored}"
