# UndoLog Examples

Framework integration examples demonstrating UndoLog's three-tier safety model,
effect logging, and compensation rollbacks.

Each example implements an identical customer support scenario with the same
four tools from the shared `example_tools` package. This allows you to compare
how different agent frameworks integrate with UndoLog side by side.

## Available Examples

| Directory | Framework | Description |
|---|---|---|
| [`langchain-support-agent/`](langchain-support-agent/) | LangChain / LangGraph | LangChain support agent using `wrap_langgraph` and tools |
| [`crewai-support-agent/`](crewai-support-agent/) | CrewAI | Multi-agent support crew with LangChain structured tool wrappers |
| [`llama-index-support-agent/`](llama-index-support-agent/) | LlamaIndex | LlamaIndex `AgentRunner` with `FunctionTool` wrappers |
| [`semantic-kernel-support-agent/`](semantic-kernel-support-agent/) | Semantic Kernel | Microsoft Semantic Kernel support plugin functions |
| [`vanilla-support-agent/`](vanilla-support-agent/) | Vanilla Asyncio | Plain Python asyncio loop calling OpenAI directly without a framework |

## Shared Tools (`example_tools`)

All example agents import tools and compensation handlers from
[`example_tools/`](example_tools/). The canonical tools cover all three
reversibility tiers:

| Tool | Tier | Behaviour |
|---|---|---|
| `lookup_customer` | SAFE | Auto-replayed: no side effects |
| `send_email` | COMPENSABLE | Compensation sends a correction email |
| `create_ticket` | COMPENSABLE | Compensation closes or voids the ticket |
| `escalate_case` | IRREVERSIBLE | Requires human approval before execution |

## Quickstart

### 1. Start the UndoLog stack

Start the Go proxy, Rust engine, and PostgreSQL backing services:

```bash
docker compose up -d
```

Or run the zero-dependency local development server:

```bash
undolog dev
```

### 2. Install the shared tools package

Install `example_tools` in editable mode:

```bash
pip install -e examples/example_tools
```

### 3. Run an example agent

Each directory contains its own `pyproject.toml` and entrypoint script:

```bash
cd examples/crewai-support-agent
pip install -e ".[dev]"
export UNDOLOG_PROXY_URL=http://localhost:8080
export OPENAI_API_KEY=sk-...
python crewai_agent.py
```

## Running Tests

Unit tests for all examples run with the shared `test-examples` target from the
repository root:

```bash
make test-examples
```

Or test an individual example:

```bash
cd examples/crewai-support-agent
pytest -v -m "not integration"
```
