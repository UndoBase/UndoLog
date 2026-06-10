# LangChain Customer Support Agent

A real-world LangChain agent integrated with UndoLog for exactly-once
execution, effect tracking, and compensation rollbacks.

## Tools

| Tool | Tier | Behaviour |
|------|------|-----------|
| `lookup_customer` | SAFE | Auto-replayed : no side effects |
| `send_email` | COMPENSABLE | Compensation sends a correction email |
| `create_ticket` | COMPENSABLE | Compensation closes or voids the ticket |
| `escalate_case` | IRREVERSIBLE | Requires human approval before execution |

## Usage

1. Start the UndoLog stack (see repo root).
2. Install dependencies:

```bash
pip install undolog-sdk langchain-openai langgraph
```

3. Set environment variables:

```bash
export UNDOLOG_PROXY_URL=http://localhost:8080
export OPENAI_API_KEY=sk-...
```

4. Run the agent:

```bash
python agent.py
```

The runner works with any OpenAI-compatible LLM provider.  To use a
different provider set ``OPENAI_BASE_URL`` and optionally ``OPENAI_MODEL``:

```bash
export OPENAI_BASE_URL=https://api.groq.com/openai/v1
export OPENAI_MODEL=llama-3.3-70b-versatile
python run_against_stack.py
```

## Testing

```bash
pip install -e ".[dev]"
pytest -v
```
