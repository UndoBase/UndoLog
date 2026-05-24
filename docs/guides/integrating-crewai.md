---
title: "Integrate UndoLog with CrewAI"
description: "## Prerequisites"
section: "guides"
---
# Integrate UndoLog with CrewAI

## Prerequisites

- UndoLog proxy running (see [Installation](../getting-started/installation.md))
- CrewAI >= 0.30.0
- Python 3.10+
- `undolog-sdk` installed (`pip install undolog-sdk`)

## What you'll build

A CrewAI crew with three agents: a researcher, a writer, and a publisher, where every tool call is protected by UndoLog's three-tier safety model. When the publisher agent triggers an irreversible action (sending a newsletter), the crew pauses and waits for human approval.

## Steps

### 1. Define your tools with UndoLog

```python
from undolog_sdk import undolog_tool, ToolTier, CompensationDescriptor, UndoLogSession, AwaitingApprovalError

@undolog_tool(tier=ToolTier.SAFE)
def search_articles(query: str) -> list[dict]:
    """Search the knowledge base (safe: read-only)."""
    return [{"title": "UndoLog in production", "url": "..."}]

@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=CompensationDescriptor.new("draft_revision", args={"reason": "content update"}))
def publish_draft(title: str, content: str) -> dict:
    """Publish a draft (compensable: can be reverted)."""
    return {"article_id": "art_123", "status": "published"}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
def send_newsletter(article_id: str) -> dict:
    """Send newsletter to all subscribers (irreversible: requires approval)."""
    return {"sent_to": 15234, "article_id": article_id}
```

### 2. Create the UndoLog session wrapper

CrewAI agents call tools directly. Wrap the agent execution in an UndoLog session to track all tool calls:

```python
import asyncio
from crewai import Agent, Task, Crew, Process

async def run_crew_with_undolog():
    async with UndoLogSession(org_id="org_prod", session_id="newsletter-campaign-42") as session:
        researcher = Agent(
            role="Researcher",
            goal="Find relevant articles",
            tools=[search_articles],
        )
        writer = Agent(
            role="Writer",
            goal="Write newsletter content",
            tools=[publish_draft],
        )
        publisher = Agent(
            role="Publisher",
            goal="Send newsletter to subscribers",
            tools=[send_newsletter],
        )

        research_task = Task(
            description="Find articles about AI safety",
            agent=researcher,
        )
        write_task = Task(
            description="Write newsletter based on research",
            agent=writer,
        )
        publish_task = Task(
            description="Send the newsletter",
            agent=publisher,
        )

        crew = Crew(
            agents=[researcher, writer, publisher],
            tasks=[research_task, publish_task],
            process=Process.sequential,
        )

        try:
            result = crew.kickoff()
            print("Crew completed:", result)
        except AwaitingApprovalError as e:
            print(f"Approval required for {e.tool_name} (approval_id: {e.approval_id})")
            print("Approve via: POST /approvals/{e.approval_id}/approve")
```

### 3. Handle the approval flow

CrewAI does not natively support mid-execution pauses. When an irreversible tool triggers `AwaitingApprovalError`, catch it and poll for approval:

```python
import time
import httpx

async def run_with_approval_polling():
    async with UndoLogSession(org_id="org_prod") as session:
        crew = Crew(agents=[publisher], tasks=[Task(description="Send newsletter", agent=publisher)], process=Process.sequential)
        while True:
            try:
                return crew.kickoff()
            except AwaitingApprovalError as e:
                print(f"Awaiting approval: {e.approval_id}")
                while True:
                    resp = httpx.get(f"http://localhost:8080/approvals/{e.approval_id}")
                    data = resp.json()
                    if data["status"] == "approved":
                        print("Approved: continuing")
                        break
                    elif data["status"] == "rejected":
                        print("Rejected: halting crew")
                        return
                    await asyncio.sleep(5)
```

## Verify it works

```bash
python crew_with_undolog.py
```

Expected output (first run, no approval yet):
```
Researcher: searching articles...
Writer: publishing draft...
Publisher: attempting to send newsletter...
AwaitingApprovalError: approval_id=apr_abc123, tool_name=send_newsletter
```

After approving via dashboard or REST:
```
Approved: continuing
Newsletter sent to 15234 subscribers
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `UndoLogClientError: Connection refused` | Proxy not running | Start the proxy: `docker compose up -d proxy` |
| Tools execute but no session tracked | Session not passed to crew | Wrap crew execution in `async with UndoLogSession(...)` |
| `AwaitingApprovalError` never raised | Tool tier not set to IRREVERSIBLE | Check `@undolog_tool(tier=ToolTier.IRREVERSIBLE)` |
| Crew crashes on approval wait | Approval timeout exceeded | Extend `default_approval_timeout_seconds` in `undolog_orgs` table |

## Next steps

- [Configure approval gates](configuring-approval-gates.md) for production approval workflows
- [Write compensations](writing-compensations.md) that handle partial crew failures
- [Run in production](running-in-production.md) with proper scaling
