"""Session management for the UndoLog Python SDK.

An ``UndoLogSession`` is an async context manager that:

- Generates a unique ``session_id`` (UUID v4) on entry.
- Holds an ``org_id`` that scopes all intercepted tool calls.
- Tracks ``step_index`` automatically, incrementing monotonically per call.

Usage::

    async with UndoLogSession(org_id="org-abc") as session:
        result = await some_tool(arg=1, _session=session)
"""

from __future__ import annotations

import sys
import types
import uuid
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self


@dataclass
class UndoLogSession:
    """Async context manager for an UndoLog session.

    The session is the source of truth for organisation identity, session
    identity, and step ordering within a single agent run.

    Args:
        org_id: Organisation identifier that scopes all intercepted tool calls.
    """

    org_id: str

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """UUID v4 string generated once per session and stable until exit."""

    _step_index: int = field(default=0, repr=False)
    """Internal step counter. Incremented by ``next_step()``."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        pass

    def next_step(self) -> int:
        """Advance and return the next step index.

        The first call returns 1, then 2, 3, … - this ensures step indices
        are human-friendly (1-based) while matching Rust's ``u32`` domain.
        """
        self._step_index += 1
        return self._step_index
