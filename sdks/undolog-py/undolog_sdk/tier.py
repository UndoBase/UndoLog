"""Tool tier classification - the core UndoLog contract.

Every tool is classified at registration time into exactly one tier.
Classification is declarative (SDK annotation), never inferred by the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolTier(Enum):
    """How the Effect Engine treats an intercepted tool call.

    Usage:
        >>> ToolTier.SAFE
        >>> ToolTier.COMPENSABLE
        >>> ToolTier.IRREVERSIBLE
    """

    SAFE = "safe"
    """Read-only or idempotent. Execute freely; no effect log entry required.

    Examples: search_web, read_file, get_user
    """

    COMPENSABLE = "compensable"
    """Write operation with a well-defined compensation (undo).

    Examples: send_email, transfer_funds, create_record

    UndoLog: compensation is registered in the undo stack *before* execution.
    """

    IRREVERSIBLE = "irreversible"
    """Cannot be undone. Requires explicit human approval before execution.

    Examples: delete_database, publish_to_production, wire_large_amount
    """

    @property
    def is_safe(self) -> bool:
        """``True`` when the tool is read-only or idempotent (no effect log entry)."""
        return self is ToolTier.SAFE

    @property
    def is_compensable(self) -> bool:
        """``True`` when the tool registers a compensation function before execution."""
        return self is ToolTier.COMPENSABLE

    @property
    def requires_approval(self) -> bool:
        """``True`` when the tool requires human approval before execution."""
        return self is ToolTier.IRREVERSIBLE


@dataclass
class CompensationDescriptor:
    """Describes the compensation function to invoke when rolling back a
    ``Compensable`` tool call.

    Stored in the undo stack entry *before* the action executes, so that
    a process crash cannot lose the compensation information.
    """

    fn_name: str
    """Logical name matching the compensation registry."""

    fn_version: str = "1.0.0"
    """Semver version of the compensation function."""

    args: dict[str, Any] = field(default_factory=dict)
    """Arguments captured from the original call *before* execution."""

    max_retries: int = 3
    """Max retry attempts before escalating to compensation_failed."""

    retry_backoff_ms: int = 1_000
    """Backoff delay between retries in milliseconds."""

    @classmethod
    def new(
        cls, fn_name: str, args: dict[str, Any] | None = None
    ) -> CompensationDescriptor:
        """Create a CompensationDescriptor with default version and retry settings."""
        return cls(fn_name=fn_name, args=args or {})


TIER_LABELS: dict[ToolTier, str] = {
    ToolTier.SAFE: "safe",
    ToolTier.COMPENSABLE: "compensable",
    ToolTier.IRREVERSIBLE: "irreversible",
}
"""Short lowercase labels - used in DB enum columns and log fields."""
