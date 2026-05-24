---
title: "Quickstart: Protect your first AI agent"
description: "In this tutorial, you build a simple AI agent that searches a knowledge base and creates user accounts. You protect each operation with the right UndoLog tier so that mistakes are caught, side effects"
section: "getting-started"
---
# Quickstart: Protect your first AI agent

In this tutorial, you build a simple AI agent that searches a knowledge base
and creates user accounts. You protect each operation with the right UndoLog
tier so that mistakes are caught, side effects are reversible, and dangerous
actions require human sign-off.

---

## What you will build

A single-file agent with three tool tiers:

| Tool | Tier | Behaviour |
|---|---|---|
| `search_knowledge_base` | SAFE | Executes immediately, no logging |
| `create_user` | COMPENSABLE | Logged; a `delete_user` compensation is registered before execution |
| `process_payout` | IRREVERSIBLE | Logged; requires human approval, the session suspends until resolved |

---

## Install the SDK

```bash
pip install undolog-sdk
```

Make sure the UndoLog stack is running (see [Installation](installation.md)).

---

## Create the agent

Save the following as `agent.py`:

```python
import asyncio
from undolog_sdk import (
    undolog_tool,
    ToolTier,
    CompensationDescriptor,
    UndoLogSession,
    AwaitingApprovalError,
)


# ── SAFE: read-only, no logging needed ──────────────────────────────────────

@undolog_tool(tier=ToolTier.SAFE)
async def search_knowledge_base(query: str) -> list[dict]:
    """Search the internal knowledge base."""
    # In production this would hit your vector DB.
    results = [
        {"title": "Onboarding workflow", "score": 0.95},
        {"title": "Account creation guide", "score": 0.88},
    ]
    return [r for r in results if query.lower() in r["title"].lower()]


# ── COMPENSABLE: logged, undo registered before execution ───────────────────

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new(
        "delete_user",
        args={"soft": True},
    ),
)
async def create_user(email: str, name: str) -> dict:
    """Create a new user account."""
    # In production this would call your identity provider.
    user_id = f"usr_{hash(email) & 0xFFFF}"
    print(f"  [create_user] Created {name} <{email}> → {user_id}")
    return {"user_id": user_id, "email": email, "name": name}


# ── IRREVERSIBLE: logged, human approval required before execution ──────────

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def process_payout(user_id: str, amount: float) -> dict:
    """Send a payout to the user."""
    print(f"  [process_payout] Paying ${amount:.2f} to {user_id}")
    return {"status": "paid", "user_id": user_id, "amount": amount}


# ── Main agent loop ─────────────────────────────────────────────────────────

async def main():
    session = UndoLogSession(org_id="org-acme")

    async with session:
        # Step 1. SAFE call: executes immediately, no log entry.
        print(">>> Searching knowledge base...")
        docs = await search_knowledge_base(query="onboarding", _session=session)
        print(f"    Found {len(docs)} document(s)")

        # Step 2. COMPENSABLE call: the proxy records an effect and pushes a
        #           compensation onto the undo stack *before* the function runs.
        print(">>> Creating user...")
        user = await create_user(
            email="alice@example.com",
            name="Alice",
            _session=session,
        )
        print(f"    Created: {user}")

        # Step 3. IRREVERSIBLE call: the proxy records the effect, creates an
        #           approval request, and raises AwaitingApprovalError.
        print(">>> Processing payout...")
        try:
            result = await process_payout(
                user_id=user["user_id"],
                amount=49.99,
                _session=session,
            )
            print(f"    Payout result: {result}")
        except AwaitingApprovalError as err:
            print(f"    ! Need approval: {err.approval_id}")
            print("    The session is suspended. Approve or reject via the")
            print("    dashboard at http://localhost:3000")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Run the agent

```bash
python agent.py
```

With the Docker stack running you see output similar to:

```
>>> Searching knowledge base...
    Found 1 document(s)
>>> Creating user...
  [create_user] Created Alice <alice@example.com> → usr_1234
    Created: {'user_id': 'usr_1234', 'email': 'alice@example.com', 'name': 'Alice'}
>>> Processing payout...
    ! Need approval: apr_abc123
    The session is suspended. Approve or reject via the
    dashboard at http://localhost:3000
```

Open **http://localhost:3000** in your browser. The dashboard shows one active
session with a pending approval for `process_payout`. You can inspect the
arguments (`user_id` and `amount`) and approve or reject the request.

---

## What you built

You wired a three-tier safety net into a real agent in about 30 lines of
decorator annotations:

- **SAFE** (`search_knowledge_base`): the tool runs immediately with zero
  overhead. No effect log entry is created, no compensation is registered.
- **COMPENSABLE** (`create_user`): the proxy logs the call and pushes a
  `delete_user` compensation onto the session's undo stack *before* `create_user`
  runs. If a later step fails, the engine walks the undo stack LIFO and calls
  each compensation.
- **IRREVERSIBLE** (`process_payout`): the proxy logs the call and creates an
  approval request. The tool never executes unless a human approves it. Your
  agent caught the `AwaitingApprovalError` gracefully and told the user where
  to go.

If any call after `create_user` had raised an exception (or if the process
crashed), the engine would invoke `delete_user` automatically, the
compensation was registered before the account was created, so it survives
crashes.

---

## Next steps

- Read [Core concepts](concepts.md) to understand the effect journal and
  compensation model in depth.
- Browse the [Integration guides](../guides/) for LangGraph, CrewAI, and
  custom framework adapters.
- Check the [API reference](../reference/api/) for the full SDK surface.
