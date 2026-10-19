# CrewAI Customer Support Agent

A customer support agent built with [CrewAI](https://github.com/crewAIInc/crewAI)
and integrated with UndoLog for exactly-once execution, effect tracking, and
compensation rollbacks via LangChain `StructuredTool` wrappers.

## Tools

| Tool | Tier | Behaviour |
|---|---|---|
| `lookup_customer` | SAFE | Auto-replayed: no side effects |
| `send_email` | COMPENSABLE | Compensation sends a correction email |
| `create_ticket` | COMPENSABLE | Compensation closes or voids the ticket |
| `escalate_case` | IRREVERSIBLE | Requires human approval before execution |

## Usage

1. Start the UndoLog stack:

```bash
docker compose up -d
```

2. Install dependencies:

```bash
pip install -e ../example_tools
pip install -e .
```

3. Set environment variables:

```bash
export UNDOLOG_PROXY_URL=http://localhost:8080
export OPENAI_API_KEY=sk-...
```

Optional configuration:
- `UNDOLOG_ORG_ID`: Organisation identifier (default `org_demo`).
- `OPENAI_MODEL`: Model name (default `gpt-4o`).
- `OPENAI_BASE_URL`: Base URL for OpenAI-compatible providers.

4. Run the agent:

```bash
python crewai_agent.py
```

## Testing

Run the unit test suite without integration dependencies:

```bash
pip install -e ".[dev]"
pytest -v -m "not integration"
```

Or using the local Makefile:

```bash
make test-unit
```
