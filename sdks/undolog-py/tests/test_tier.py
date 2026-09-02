"""Tests for ToolTier and CompensationDescriptor.

Covers tier property classification, wire-value serialization,
label mapping, and descriptor field defaults and construction.
CompensationDescriptor equality and retry invariants are tested in
``test_state_machine.py``; this file focuses on tier properties and
descriptor field contracts.
"""

from __future__ import annotations

from undolog_sdk.tier import TIER_LABELS, CompensationDescriptor, ToolTier

# ── ToolTier properties ────────────────────────────────────────────────────


class TestToolTierProperties:
    """Each tier exposes exactly one boolean property as ``True``."""

    def test_safe_is_safe(self) -> None:
        assert ToolTier.SAFE.is_safe is True
        assert ToolTier.SAFE.is_compensable is False
        assert ToolTier.SAFE.requires_approval is False

    def test_compensable_is_compensable(self) -> None:
        assert ToolTier.COMPENSABLE.is_safe is False
        assert ToolTier.COMPENSABLE.is_compensable is True
        assert ToolTier.COMPENSABLE.requires_approval is False

    def test_irreversible_requires_approval(self) -> None:
        assert ToolTier.IRREVERSIBLE.is_safe is False
        assert ToolTier.IRREVERSIBLE.is_compensable is False
        assert ToolTier.IRREVERSIBLE.requires_approval is True


# ── ToolTier wire values ───────────────────────────────────────────────────


class TestToolTierWireValues:
    """Enum values match the wire protocol strings."""

    def test_safe_value(self) -> None:
        assert ToolTier.SAFE.value == "safe"

    def test_compensable_value(self) -> None:
        assert ToolTier.COMPENSABLE.value == "compensable"

    def test_irreversible_value(self) -> None:
        assert ToolTier.IRREVERSIBLE.value == "irreversible"

    def test_all_three_tiers(self) -> None:
        assert len(ToolTier) == 3


# ── TIER_LABELS ────────────────────────────────────────────────────────────


class TestTierLabels:
    """``TIER_LABELS`` maps every tier to its lowercase wire string."""

    def test_labels_match_values(self) -> None:
        for tier in ToolTier:
            assert TIER_LABELS[tier] == tier.value

    def test_label_count_matches_tier_count(self) -> None:
        assert len(TIER_LABELS) == len(ToolTier)


# ── CompensationDescriptor construction ────────────────────────────────────


class TestCompensationDescriptorFields:
    """Descriptor field defaults and the ``new()`` classmethod."""

    def test_new_defaults(self) -> None:
        d = CompensationDescriptor.new("undo_send_email")
        assert d.fn_name == "undo_send_email"
        assert d.fn_version == "1.0.0"
        assert d.args == {}
        assert d.max_retries == 3
        assert d.retry_backoff_ms == 1000

    def test_new_with_args(self) -> None:
        d = CompensationDescriptor.new("undo_transfer", args={"tx_id": "abc"})
        assert d.args == {"tx_id": "abc"}

    def test_direct_construction(self) -> None:
        d = CompensationDescriptor(
            fn_name="undo_fn",
            fn_version="2.0.0",
            args={"id": "1"},
            max_retries=5,
            retry_backoff_ms=2000,
        )
        assert d.fn_version == "2.0.0"
        assert d.max_retries == 5
        assert d.retry_backoff_ms == 2000

    def test_descriptor_is_dataclass(self) -> None:
        d = CompensationDescriptor.new("fn")
        assert hasattr(d, "__dataclass_fields__")
