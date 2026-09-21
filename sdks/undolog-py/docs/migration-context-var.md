# Migrating to Context-Var Session Injection

The `_session` parameter is now optional. Tools can resolve the session
from a context var set by `run_with_session`.

## Before

```python
from undolog_sdk import undolog_tool, ToolTier
from undolog_sdk.session import UndoLogSession


@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=...)
async def send_email(to: str, subject: str) -> dict:
    return {"status": "sent"}


async with UndoLogSession(org_id="org-abc") as session:
    await send_email(to="alice@example.com", subject="Hi", _session=session)
```

## After

```python
from undolog_sdk import undolog_tool, ToolTier, run_with_session
from undolog_sdk.session import UndoLogSession


@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=...)
async def send_email(to: str, subject: str) -> dict:
    return {"status": "sent"}


async with UndoLogSession(org_id="org-abc") as session:
    async with run_with_session(session):
        await send_email(to="alice@example.com", subject="Hi")
```

## What changed

- `_session=session` is no longer required in every call.
- `run_with_session(session)` sets the session for all tools in the block.
- Explicit `_session=session` still works and takes precedence.
- `get_current_session()` returns the session or `None`.
- `require_current_session()` returns the session or raises `RuntimeError`.

## Why migrate

- Cleaner tool signatures (no `_session` parameter).
- Framework compatibility (LangGraph, CrewAI, etc.).
- No risk of forgetting `_session` in some calls.

## Backward compatibility

Existing code with `_session=session` continues to work. Migration is
optional but recommended for new code.
