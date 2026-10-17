---
title: "Quickstart: Protect your first AI agent"
description: "Build an AI agent that searches a knowledge base, creates user accounts, and processes payouts with UndoLog tool tiers and local dev mode."
section: "getting-started"
---
# Quickstart: Protect your first AI agent

In this tutorial, you build an AI agent that searches a knowledge base, creates
user accounts, and processes payouts. You protect each operation with the
appropriate UndoLog tier so that mistakes are caught, side effects are
reversible, and irreversible operations require human sign-off.

You will run this quickstart entirely locally using `undolog dev`: no Docker,
PostgreSQL, or API keys are required.

For durable deployments with PostgreSQL and Docker Compose, see the
[Installation Guide](installation.md).

---

## What you will build

A single-file agent with three tool tiers:

| Tool | Tier | Behaviour |
|---|---|---|
| `search_knowledge_base` | SAFE | Executes immediately in-process without logging |
| `create_user` | COMPENSABLE | Logged: a `delete_user` compensation is registered before execution |
| `process_payout` | IRREVERSIBLE | Logged: requires human approval before execution |

For TypeScript and JavaScript agents, install `@undolog/sdk` (see the
[TypeScript SDK README](../../sdks/undolog-ts/README.md)).

---

## 1. Install the SDK

Install the UndoLog Python SDK:

```bash
pip install undolog-sdk
```

---

## 2. Start the local development server

UndoLog includes a lightweight development server that speaks the proxy HTTP
contract, backed by a local SQLite journal:

```bash
undolog dev --irreversible "process_payout"
```

The server listens on `http://127.0.0.1:8080`. The SDK connects to this address
by default when `UNDOLOG_PROXY_URL` is unset. The `--irreversible` pattern marks
`process_payout` as requiring human approval before it executes.

Keep this terminal open, and run the agent in a second terminal.

---

## 3. Create the agent

Save the following code as `agent.py`:

```python
import asyncio
from undolog_sdk import (
    AwaitingApprovalError,
    CompensationDescriptor,
    ToolTier,
    UndoLogSession,
    run_with_session,
    undolog_tool,
)


# -- SAFE: read-only, no logging needed --------------------------------------

@undolog_tool(tier=ToolTier.SAFE)
async def search_knowledge_base(query: str) -> list[dict]:
    """Search the internal knowledge base."""
    # In production this would query your vector database.
    results = [
        {"title": "Onboarding workflow", "score": 0.95},
        {"title": "Account creation guide", "score": 0.88},
    ]
    return [r for r in results if query.lower() in r["title"].lower()]


# -- COMPENSABLE: logged, undo registered before execution -------------------

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
    print(f"  [create_user] Created {name} <{email}> -> {user_id}")
    return {"user_id": user_id, "email": email, "name": name}


# -- IRREVERSIBLE: logged, human approval required before execution ----------

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def process_payout(user_id: str, amount: float) -> dict:
    """Send a payout to the user."""
    print(f"  [process_payout] Paying ${amount:.2f} to {user_id}")
    return {"status": "paid", "user_id": user_id, "amount": amount}


# -- Main agent loop ---------------------------------------------------------

async def main():
    async with UndoLogSession(org_id="org-acme") as session:
        async with run_with_session(session):
            # Step 1. SAFE call: executes immediately, no log entry.
            print(">>> Searching knowledge base...")
            docs = await search_knowledge_base(query="onboarding")
            print(f"    Found {len(docs)} document(s)")

            # Step 2. COMPENSABLE call: the server records an effect and pushes
            #         a compensation onto the undo stack before the function runs.
            print(">>> Creating user...")
            user = await create_user(
                email="alice@example.com",
                name="Alice",
            )
            print(f"    Created: {user}")

            # Step 3. IRREVERSIBLE call: the server records the effect, creates
            #         an approval request, and raises AwaitingApprovalError.
            print(">>> Processing payout...")
            try:
                result = await process_payout(
                    user_id=user["user_id"],
                    amount=49.99,
                )
                print(f"    Payout result: {result}")
            except AwaitingApprovalError as err:
                print(f"    ! Need approval: {err.approval_id}")
                print("    The session is suspended until resolved.")
                print("    Approve via curl:")
                print(
                    f"      curl -X POST http://localhost:8080/approvals/{err.approval_id}/approve"
                )


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 4. Run the agent

Run the agent in a second terminal:

```bash
python agent.py
```

Output:

```text
>>> Searching knowledge base...
    Found 1 document(s)
>>> Creating user...
  [create_user] Created Alice <alice@example.com> -> usr_27306
    Created: {'user_id': 'usr_27306', 'email': 'alice@example.com', 'name': 'Alice'}
>>> Processing payout...
    ! Need approval: e38bc49a-9676-405d-aec1-308a5a80cf89
    The session is suspended until resolved.
    Approve via curl:
      curl -X POST http://localhost:8080/approvals/e38bc49a-9676-405d-aec1-308a5a80cf89/approve
```

---

## 5. Inspect and approve the request

List pending approvals from the development server:

```bash
curl http://localhost:8080/approvals
```

Response:

```json
[
  {
    "id": "e38bc49a-9676-405d-aec1-308a5a80cf89",
    "effect_id": "...",
    "session_id": "...",
    "tool_name": "process_payout",
    "status": "pending",
    "created_at": "..."
  }
]
```

Approve the pending payout using the command printed by the agent:

```bash
curl -X POST http://localhost:8080/approvals/e38bc49a-9676-405d-aec1-308a5a80cf89/approve
```

The server returns:

```json
{"status":"approved","approval_id":"e38bc49a-9676-405d-aec1-308a5a80cf89"}
```

When running the full Docker Compose stack (see [Installation](installation.md)),
pending approvals can also be inspected and resolved in the experimental web
dashboard at `http://localhost:3000`.

---

## What you built

You wired a three-tier safety net into an agent in about 30 lines of
declarative annotations:

- **SAFE** (`search_knowledge_base`): runs immediately in-process with zero
  network overhead. No journal entry is created, and no compensation is
  registered.
- **COMPENSABLE** (`create_user`): the server journals the call and registers
  the `delete_user` compensation *before* `create_user` executes. If a later
  step fails, the engine executes compensations in reverse order (LIFO).
- **IRREVERSIBLE** (`process_payout`): the server journals the call and holds
  it for approval. The function never executes without human sign-off. The
  agent caught `AwaitingApprovalError` gracefully.

Because the compensation is registered before execution, it survives process
crashes and network interruptions.

---

## Next steps

- [Installation](installation.md): deploy the persistent Docker Compose stack with PostgreSQL and the Rust engine.
- [Core concepts](concepts.md): learn how the effect journal and exactly-once execution engine work.
- [Integration guides](../guides/): connect UndoLog with LangGraph, CrewAI, and other agent frameworks.
- [Python SDK Reference](../reference/api/python-sdk.md): explore decorators, context managers, and testing harnesses.
