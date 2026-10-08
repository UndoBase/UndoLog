---
title: "Integrate UndoLog with CrewAI"
description: "Wire UndoLog interception, compensations, and approval gates into a CrewAI crew in a few lines."
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

CrewAI does not natively support mid-execution pauses. When an irreversible
tool triggers `AwaitingApprovalError`, surface the approval identifier and
pause. The human approves via the dashboard (`GET /events` SSE stream) or via
`POST /approvals/{id}/approve`. Once the approval is resolved, retry the same
tool call; the engine replays the cached result instead of re-executing.

```python
from undolog_sdk import AwaitingApprovalError

def run_crew_with_approval():
    with UndoLogSession(org_id="org_prod") as session:
        crew = Crew(agents=[publisher], tasks=[Task(description="Send newsletter", agent=publisher)], process=Process.sequential)
        try:
            return crew.kickoff()
        except AwaitingApprovalError as e:
            print(f"Awaiting approval: {e.approval_id}")
            print("Approve via the dashboard or POST /approvals/" + e.approval_id + "/approve")
            # After human approval, retry the same tool call.
            # The engine replays the cached result (no re-execution).
            return crew.kickoff()
```

## Alternative: wrap the crew

`wrap_crewai` opens one session for the run, instruments every agent's
tools, and sets the context variable the tools read their session from,
so no session has to be threaded through the crew by hand:

```python
from undolog_sdk.integrations import wrap_crewai

crew = wrap_crewai(crew, org_id="org_prod")
result = await crew.kickoff_async()
```

Tools already decorated with `@undolog_tool` come back untouched. Raw
tools need a classification, because `wrap_crewai` assigns one at
kickoff:

```python
crew = wrap_crewai(
    crew,
    org_id="org_prod",
    tiers={
        "search_articles": ToolTier.SAFE,
        "send_newsletter": ToolTier.IRREVERSIBLE,
    },
    compensations={"publish_draft": "draft_revision"},
)
```

An Irreversible tool that needs a human raises `AwaitingApprovalError`
out of `kickoff_async`. The facade records the run before it propagates,
so the handler already has what it needs to resume:

```python
try:
    result = await crew.kickoff_async()
except AwaitingApprovalError as exc:
    print(f"Approve via the dashboard or POST /approvals/{exc.approval_id}/approve")
    result = await crew.kickoff_async(inputs={"session_id": crew.session_id})
```

`session_id` and `undolog_step_index` are consumed by the wrapper and
never reach the crew's tasks. Resuming with `session_id` alone restarts
the step counter, so a retried run's calls land on the steps they
already journaled: the engine replays the ones that completed and lets
the approved call through, instead of running everything again. Add
`undolog_step_index` (`crew.step_index`) to continue past those steps
and start new work in the same journal instead.

Four rules:

- Only `kickoff_async` is instrumented: it opens the session and wraps
  the tools. A synchronous `crew.kickoff()` gets neither, so a
  decorated tool raises `RuntimeError` for the missing session.
- Tools must be async. `undolog_tool` awaits the function it wraps, so
  `wrap_crewai` rejects a sync tool with `ValueError` rather than
  wrapping one that fails at its first call.
- A raw tool that is not listed in `tiers` defaults to `COMPENSABLE`,
  and `wrap_crewai` raises `ValueError` when it has no compensation
  name: it would then run with no journal, no compensation, and no
  approval gate.
- The wrap is idempotent, so a second kickoff neither re-wraps nor
  journals a call twice.

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
| `ValueError: wrap_crewai cannot instrument ...` | Tool is a sync function | Declare the tool `async def` |
| `ValueError: Compensable tool ... has no compensation name` | Raw tool with no tier or compensation | List it in `tiers=` or `compensations=` |
| `kickoff_async` returned while a tool awaits approval | The agent executor caught `AwaitingApprovalError` | Find the `approval_required` warning in the logs; the approval is already in the engine |
| Crew crashes on approval wait | Approval timeout exceeded | Extend `default_approval_timeout_seconds` in `undolog_orgs` table |

## Next steps

- [Configure approval gates](configuring-approval-gates.md) for production approval workflows
- [Write compensations](writing-compensations.md) that handle partial crew failures
- [Run in production](running-in-production.md) with proper scaling
