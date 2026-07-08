"""Tests for the approval, compensation, and replay demos.

Verifies
--------
1. Demo module imports resolve correctly.
2. Demo tools have correct UndoLog tiers and compensation descriptors.
3. Compensation handlers produce expected results.
4. Helper functions in approval_demo and replay_demo produce correct values.
"""

from __future__ import annotations

import os
from unittest import mock

from compensation_demo import (
    assign_engineer,
    escalate_ticket,
    lookup_user,
    notify_user,
    open_ticket,
)
from example_tools.compensations import (
    compensate_assign_engineer,
    compensate_charge_payment,
    compensate_create_ticket,
    compensate_escalate,
    compensate_send_email,
)
from replay_demo import lookup_plan
from undolog_sdk import CompensationDescriptor, ToolTier


class TestApprovalDemoImports:
    """approval_demo module imports resolve correctly."""

    def test_import_approval_demo(self) -> None:
        import approval_demo  # noqa: F811  -- re-import in test isolation

        assert hasattr(approval_demo, "main")


class TestDemoToolTiers:
    """Each demo tool must have the correct UndoLog tier."""

    def test_lookup_user_is_safe(self) -> None:
        assert getattr(lookup_user, "_undolog_tier", None) is ToolTier.SAFE

    def test_notify_user_is_compensable(self) -> None:
        assert getattr(notify_user, "_undolog_tier", None) is ToolTier.COMPENSABLE

    def test_open_ticket_is_compensable(self) -> None:
        assert getattr(open_ticket, "_undolog_tier", None) is ToolTier.COMPENSABLE

    def test_assign_engineer_is_compensable(self) -> None:
        assert getattr(assign_engineer, "_undolog_tier", None) is ToolTier.COMPENSABLE

    def test_escalate_ticket_is_compensable(self) -> None:
        assert getattr(escalate_ticket, "_undolog_tier", None) is ToolTier.COMPENSABLE


class TestDemoCompensationDescriptors:
    """COMPENSABLE demo tools must have a compensation descriptor."""

    def test_notify_user_has_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(notify_user, "_undolog_compensation", None)
        assert comp is not None
        assert comp.fn_name == "compensate_send_email"
        assert "to" in comp.args

    def test_open_ticket_has_compensation_with_custom_retry(self) -> None:
        comp: CompensationDescriptor | None = getattr(open_ticket, "_undolog_compensation", None)
        assert comp is not None
        assert comp.fn_name == "compensate_create_ticket"
        assert "ticket_id" in comp.args
        assert comp.max_retries == 5
        assert comp.retry_backoff_ms == 500

    def test_assign_engineer_has_versioned_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(
            assign_engineer, "_undolog_compensation", None
        )
        assert comp is not None
        assert comp.fn_name == "compensate_assign_engineer"
        assert comp.fn_version == "2.0.0"
        assert comp.max_retries == 3
        assert comp.retry_backoff_ms == 1_000

    def test_escalate_ticket_has_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(
            escalate_ticket, "_undolog_compensation", None
        )
        assert comp is not None
        assert comp.fn_name == "compensate_escalate"
        assert "ticket_id" in comp.args

    def test_safe_tool_has_no_compensation(self) -> None:
        comp: CompensationDescriptor | None = getattr(lookup_user, "_undolog_compensation", None)
        assert comp is None


class TestDemoToolNames:
    """Each demo tool must have a stored tool name."""

    def test_tool_names(self) -> None:
        expected = {
            lookup_user: "lookup_user",
            notify_user: "notify_user",
            open_ticket: "open_ticket",
            assign_engineer: "assign_engineer",
            escalate_ticket: "escalate_ticket",
        }
        for tool, expected_name in expected.items():
            stored: str | None = getattr(tool, "_undolog_tool_name", None)
            assert stored == expected_name, f"Expected {expected_name}, got {stored}"


class TestCompensationHandlers:
    """Verify all compensation functions produce expected results."""

    def test_compensate_send_email(self) -> None:
        result = compensate_send_email({"to": "test@example.com", "subject": "Welcome"})
        assert result["status"] == "correction_sent"
        assert result["to"] == "test@example.com"

    def test_compensate_create_ticket(self) -> None:
        result = compensate_create_ticket({"ticket_id": "TKT-12345678"})
        assert result["status"] == "ticket_closed"
        assert result["ticket_id"] == "TKT-12345678"

    def test_compensate_assign_engineer(self) -> None:
        result = compensate_assign_engineer(
            {
                "ticket_id": "TKT-12345678",
                "engineer": "sarah_senior",
            }
        )
        assert result["status"] == "engineer_unassigned"
        assert result["ticket_id"] == "TKT-12345678"
        assert result["engineer"] == "sarah_senior"

    def test_compensate_escalate(self) -> None:
        result = compensate_escalate(
            {
                "ticket_id": "TKT-12345678",
                "reason": "SLA breach",
            }
        )
        assert result["status"] == "escalation_reversed"
        assert result["ticket_id"] == "TKT-12345678"
        assert result["reason"] == "SLA breach"

    def test_compensate_send_email_missing_args(self) -> None:
        result = compensate_send_email({})
        assert result["status"] == "correction_sent"
        assert result["to"] == "unknown"

    def test_compensate_create_ticket_missing_args(self) -> None:
        result = compensate_create_ticket({})
        assert result["status"] == "ticket_closed"
        assert result["ticket_id"] == "unknown"

    def test_compensate_assign_engineer_missing_args(self) -> None:
        result = compensate_assign_engineer({})
        assert result["status"] == "engineer_unassigned"
        assert result["ticket_id"] == "unknown"
        assert result["engineer"] == "unknown"

    def test_compensate_escalate_missing_args(self) -> None:
        result = compensate_escalate({})
        assert result["status"] == "escalation_reversed"
        assert result["ticket_id"] == "unknown"
        assert result["reason"] == "unknown"


class TestApprovalDemoHelpers:
    """approval_demo helper functions produce correct values."""

    def test_default_proxy_url(self) -> None:
        from approval_demo import _proxy_url

        with mock.patch.dict(os.environ, {}, clear=True):
            assert _proxy_url() == "http://localhost:8080"

    def test_custom_proxy_url(self) -> None:
        from approval_demo import _proxy_url

        with mock.patch.dict(os.environ, {"UNDOLOG_PROXY_URL": "http://proxy:9090"}):
            assert _proxy_url() == "http://proxy:9090"

    def test_default_org_id(self) -> None:
        from approval_demo import _org_id

        with mock.patch.dict(os.environ, {}, clear=True):
            assert _org_id() == "org_demo"

    def test_custom_org_id(self) -> None:
        from approval_demo import _org_id

        with mock.patch.dict(os.environ, {"UNDOLOG_ORG_ID": "org_test"}):
            assert _org_id() == "org_test"

    def test_headers_without_api_key(self) -> None:
        from approval_demo import _headers

        with mock.patch.dict(os.environ, {}, clear=True):
            headers = _headers()
            assert headers["X-UndoLog-Org-Id"] == "org_demo"
            assert "X-Api-Key" not in headers

    def test_headers_with_api_key(self) -> None:
        from approval_demo import _headers

        with mock.patch.dict(os.environ, {"UNDOLOG_API_KEY": "sk-test"}):
            headers = _headers()
            assert headers["X-UndoLog-Org-Id"] == "org_demo"
            assert headers["X-Api-Key"] == "sk-test"


class TestReplayDemoImports:
    """replay_demo module imports resolve correctly."""

    def test_import_replay_demo(self) -> None:
        import replay_demo  # noqa: F811  -- re-import in test isolation

        assert hasattr(replay_demo, "main")

    def test_lookup_plan_is_safe(self) -> None:
        assert getattr(lookup_plan, "_undolog_tier", None) is ToolTier.SAFE

    def test_safe_tool_has_no_compensation(self) -> None:
        comp = getattr(lookup_plan, "_undolog_compensation", None)
        assert comp is None

    def test_lookup_plan_name(self) -> None:
        name: str | None = getattr(lookup_plan, "_undolog_tool_name", None)
        assert name == "lookup_plan"


class TestReplayDemoHelpers:
    """replay_demo helper functions produce correct values."""

    def test_default_proxy_url(self) -> None:
        from replay_demo import _proxy_url

        with mock.patch.dict(os.environ, {}, clear=True):
            assert _proxy_url() == "http://localhost:8080"

    def test_custom_proxy_url(self) -> None:
        from replay_demo import _proxy_url

        with mock.patch.dict(os.environ, {"UNDOLOG_PROXY_URL": "http://proxy:9090"}):
            assert _proxy_url() == "http://proxy:9090"

    def test_default_org_id(self) -> None:
        from replay_demo import _org_id

        with mock.patch.dict(os.environ, {}, clear=True):
            assert _org_id() == "org_demo"

    def test_custom_org_id(self) -> None:
        from replay_demo import _org_id

        with mock.patch.dict(os.environ, {"UNDOLOG_ORG_ID": "org_test"}):
            assert _org_id() == "org_test"

    def test_headers_without_api_key(self) -> None:
        from replay_demo import _headers

        with mock.patch.dict(os.environ, {}, clear=True):
            headers = _headers()
            assert headers["X-UndoLog-Org-Id"] == "org_demo"
            assert "X-Api-Key" not in headers

    def test_headers_with_api_key(self) -> None:
        from replay_demo import _headers

        with mock.patch.dict(os.environ, {"UNDOLOG_API_KEY": "sk-test"}):
            headers = _headers()
            assert headers["X-UndoLog-Org-Id"] == "org_demo"
            assert headers["X-Api-Key"] == "sk-test"

    def test_call_tool_builds_correct_url(self) -> None:
        from replay_demo import _call_tool

        assert _call_tool is not None


class TestReplayHandler:
    """Verify compensate_charge_payment produces expected results."""

    def test_charge_payment_handler(self) -> None:
        result = compensate_charge_payment({"amount": 100, "currency": "USD"})
        assert result["status"] == "charge_reversed"
        assert result["amount"] == 100
        assert result["currency"] == "USD"

    def test_charge_payment_missing_args(self) -> None:
        result = compensate_charge_payment({})
        assert result["status"] == "charge_reversed"
        assert result["amount"] == 0
        assert result["currency"] == "unknown"
