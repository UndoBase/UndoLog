<div align="center">

  <img src="https://raw.githubusercontent.com/UndoBase/UndoLog/main/banner.png" alt="UndoLog"/>


  <p>
    <a href="https://undolog.undobase.com">Website</a>
    · <a href="https://github.com/UndoBase/UndoLog/issues">Issues</a>
  </p>
</div>

# UndoLog Python SDK

UndoLog treats every tool call like a database transaction: classify it by
reversibility, journal it to a persistent effect log, register compensations
before execution, and gate irreversible operations behind human approval.
This package is the Python SDK: the `undolog_tool` decorator, sessions
through `run_with_session`, an async client for the UndoLog proxy, and
in-process harnesses that test compensation code without a running engine.
Your tool keeps its signature and return value: nothing in your framework
is patched.

## Install

```bash
pip install undolog-sdk
```

Python 3.10 and later. The SDK talks to the UndoLog proxy over HTTP; the
[installation guide](https://undolog.undobase.com/docs/getting-started/installation)
sets up the full Docker stack.

## Run a local server

```bash
undolog dev
```

`undolog dev` starts a single-process server on `http://127.0.0.1:8080`
with no configuration, which is where the client points by default. It has
no authentication and is meant for local development, not production.
Everything below runs against it.

## Protect a tool call

Save this as `agent.py`:

```python
import asyncio

from undolog_sdk import (
    CompensationDescriptor,
    ToolTier,
    UndoLogSession,
    run_with_session,
    undolog_tool,
)


@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> list[str]:
    return [f"result for {query}"]


@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_send_email"),
)
async def send_email(to: str) -> dict:
    return {"status": "sent", "to": to}


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        async with run_with_session(session):
            print(await search_web(query="undolog"))
            print(await send_email(to="alice@example.com"))


asyncio.run(main())
```

Run it with the local server up:

```bash
python agent.py
```

```
['result for undolog']
{'status': 'sent', 'to': 'alice@example.com'}
```

The SAFE tool runs directly with no journal entry. The COMPENSABLE tool
sends its call to the server, which journals the effect and registers the
`undo_send_email` compensation before the function body runs, then records
the result when it returns.

## Sessions

`run_with_session` publishes the session to a context variable that every
decorated tool reads, so no session keyword has to travel through every
layer of your agent:

```python
import asyncio

from undolog_sdk import (
    ToolTier,
    UndoLogSession,
    get_current_session,
    run_with_session,
    undolog_tool,
)


@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> list[str]:
    return [f"result for {query}"]


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        async with run_with_session(session):
            assert get_current_session() is session
            print(await search_web(query="undolog"))
            # An explicit argument still wins for code that already
            # holds the session.
            print(await search_web(query="undolog", _session=session))


asyncio.run(main())
```

`get_current_session()` returns the active session or `None`, and
`require_current_session()` raises `RuntimeError` when no session is set.
Every tool call, safe or not, needs a session: the context variable or the
explicit `_session` argument supplies it.

Migrating a codebase that threads `_session` explicitly? The [migration
guide](https://undolog.undobase.com/docs/guides/migrating-session-injection)
moves it over one call site at a time, with both styles working throughout.

## Framework wrappers

```python
import asyncio

from undolog_sdk import ToolTier, UndoLogSession, run_with_session
from undolog_sdk.integrations import wrap_tools


async def lookup_order(order_id: str) -> dict:
    return {"order_id": order_id, "status": "shipped"}


async def refund_order(order_id: str) -> dict:
    return {"refunded": order_id}


lookup_order, refund_order = wrap_tools(
    [lookup_order, refund_order],
    tiers={"lookup_order": ToolTier.SAFE, "refund_order": ToolTier.COMPENSABLE},
    compensations={"refund_order": "undo_refund"},
)


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        async with run_with_session(session):
            print(await lookup_order(order_id="ord_1"))


asyncio.run(main())
```

`wrap_tools` classifies raw async tools at assembly time: the list it
returns is what you hand to your framework, because a framework that
already holds a reference to the old function cannot be re-instrumented
afterwards. Each framework also gets a one-call wrapper that instruments
its tools, opens one session for the run, and surfaces a pending approval
to its caller: `wrap_langgraph`, `wrap_crewai`, `wrap_llamaindex`, and
`wrap_semantic_kernel`, all from `undolog_sdk.integrations`. A compiled
graph enters as `wrap_langgraph(graph, org_id="org-demo")`, and the
`org_id` argument defaults to `UNDOLOG_ORG_ID`, then `org_demo`. The
guides cover each framework in detail:

- [LangGraph](https://undolog.undobase.com/docs/guides/integrating-langgraph)
- [CrewAI](https://undolog.undobase.com/docs/guides/integrating-crewai)
- [LlamaIndex](https://undolog.undobase.com/docs/guides/integrating-llamaindex)
- [Semantic Kernel](https://undolog.undobase.com/docs/guides/integrating-semantic-kernel)

## Approvals and errors

An IRREVERSIBLE tool does not run until a human approves it. The call
raises `AwaitingApprovalError`, the tool body never executes, and the
error carries the `approval_id` to show a person. The local server gates
the tools you name with an `--irreversible` fnmatch pattern:

```bash
undolog dev --irreversible "*delete_record*"
```

```python
import asyncio

from undolog_sdk import (
    AwaitingApprovalError,
    ToolTier,
    UndoLogError,
    UndoLogSession,
    run_with_session,
    undolog_tool,
)


@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_record(record_id: str) -> dict:
    return {"deleted": record_id}


async def main() -> None:
    async with UndoLogSession(org_id="org-demo") as session:
        async with run_with_session(session):
            try:
                print(await delete_record(record_id="rec_1"))
            except AwaitingApprovalError as err:
                # Waiting for a human, not failing: surface the id and
                # stop until the approval is resolved.
                print(f"approval required: {err.approval_id}")
            except UndoLogError:
                # ConnectionError, TimeoutError, AuthenticationError,
                # and ServerError all arrive here.
                raise


asyncio.run(main())
```

Transport failures all derive from `UndoLogError`: `ConnectionError` when
the server is unreachable, `TimeoutError` when it does not answer in time,
`AuthenticationError` when the API key is rejected, and `ServerError` for
5xx answers. Import them from `undolog_sdk`: the first two shadow Python
builtins. `AwaitingApprovalError` is a `RuntimeError` instead, because the
call is waiting, not failing, so catch it separately.

## Test compensations without an engine

`test_compensation` runs a compensation twice with identical arguments and
requires both runs to agree, which is the idempotency contract the engine
relies on when it retries. `test_saga` rolls back a recorded set of effects
in LIFO order and reports every step. Neither needs a proxy, an engine, or
a database:

```python
import pytest

from undolog_sdk import EffectDescriptor, test_compensation, test_saga


async def undo_refund(order_id: str) -> str:
    return f"refunded {order_id}"


async def undo_notify(order_id: str) -> str:
    return f"notified about {order_id}"


@pytest.mark.asyncio
async def test_compensation_is_idempotent() -> None:
    report = await test_compensation(undo_refund, "ord_1")
    assert report.final_state == "compensated"
    assert report.execution_count == 2


@pytest.mark.asyncio
async def test_rollback_runs_lifo() -> None:
    report = await test_saga(
        [
            EffectDescriptor("refund", undo_refund, stack_position=1, args=("ord_1",)),
            EffectDescriptor("notify", undo_notify, stack_position=2, args=("ord_1",)),
        ]
    )
    assert report.session_result == "compensated"
    assert report.compensation_order == ("notify", "refund")
```

```bash
pip install undolog-sdk[test]
python -m pytest test_compensations.py
```

## Configuration

`UndoLogClient` reads `UNDOLOG_PROXY_URL` (default
`http://localhost:8080`) and `UNDOLOG_API_KEY`. The framework wrappers
read `UNDOLOG_ORG_ID` when you do not pass `org_id`. Every variable with
its default is listed in the
[configuration reference](https://undolog.undobase.com/docs/reference/configuration).

## Documentation

- [Quickstart](https://undolog.undobase.com/docs/getting-started/quickstart)
- [Core concepts](https://undolog.undobase.com/docs/getting-started/concepts)
- [Python SDK reference](https://undolog.undobase.com/docs/reference/api/python-sdk)
- [Annotating tools](https://undolog.undobase.com/docs/guides/annotating-tools)
- [Writing compensations](https://undolog.undobase.com/docs/guides/writing-compensations)
- [Approval gates](https://undolog.undobase.com/docs/guides/configuring-approval-gates)
- [All guides](https://undolog.undobase.com/docs/guides)

## Contributing

UndoLog is Apache 2.0 licensed. Contributions are welcome. Read the
[contributing guide](https://github.com/UndoBase/UndoLog/blob/main/docs/contributing/CONTRIBUTING.md)
to get started. Found a bug or have an idea?
[Open an issue](https://github.com/UndoBase/UndoLog/issues).

## Security

If you discover a security vulnerability, email
[security@undobase.com](mailto:security@undobase.com). All reports are
promptly addressed.
