---
title: "Python SDK Reference"
description: "**Package:** `undolog_sdk` **Module paths:** `undolog_sdk.client`, `undolog_sdk.session`, `undolog_sdk.decorators`, `undolog_sdk.tier`, `undolog_sdk.signature`, `undolog_sdk.integrations`, `undolog_sdk.test_harness`"
section: "reference"
---
# Python SDK Reference

**Package:** `undolog_sdk`  
**Module paths:** `undolog_sdk.client`, `undolog_sdk.session`, `undolog_sdk.decorators`, `undolog_sdk.tier`, `undolog_sdk.signature`, `undolog_sdk.integrations`, `undolog_sdk.test_harness`

---

## `UndoLogClient`

**Module:** `undolog_sdk.client`

Async HTTP client for the UndoLog MCP Proxy. Communicates with the Go proxy for tool call interception, commit, and fail reporting. Proxy errors surface as the SDK's `UndoLogError` subclasses from `undolog_sdk.errors`: `AuthenticationError`, `ServerError`, `TimeoutError`, and `ConnectionError`.

### Constructor

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proxy_url` | `str \| None` | `None` | Base URL of the UndoLog proxy. Falls back to `UNDOLOG_PROXY_URL` env var, then `http://localhost:8080`. |
| `api_key` | `str \| None` | `None` | API key sent as `X-Api-Key`. Falls back to `UNDOLOG_API_KEY` env var, then the empty string. |
| `http_client` | `httpx.AsyncClient \| None` | `None` | Pre-configured HTTP client. If omitted, a default client is created with `base_url=proxy_url` and `timeout=30.0`. |

### Methods

#### `intercept`

```text
async def intercept(
    self,
    org_id: str,
    session_id: str,
    tool_name: str,
    step_index: int,
    args: dict[str, Any],
) -> InterceptResponse
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `org_id` | `str` | Organisation scoping the call. |
| `session_id` | `str` | Active session UUID. |
| `tool_name` | `str` | Logical name of the tool. |
| `step_index` | `int` | Call order within the session. |
| `args` | `dict[str, Any]` | Tool arguments as a JSON-compatible dict. |

| Returns | Description |
|---------|-------------|
| `InterceptResponse` | Decision from the engine indicating Execute, Replay, or AwaitingApproval. |

| Exception | Condition |
|-----------|-----------|
| `AuthenticationError` | Proxy rejected the credentials (401/403). |
| `ServerError` | Proxy-side failure (5xx). |
| `TimeoutError` | Request timed out. |
| `ConnectionError` | Could not reach the proxy. |

**HTTP:** `POST /mcp/tool_call` with headers `X-UndoLog-Org-Id`, `X-UndoLog-Session-Id`, and `X-Api-Key`.

---

#### `commit`

```text
async def commit(
    self,
    org_id: str,
    session_id: str,
    effect_id: str,
    result: dict[str, Any],
) -> dict[str, Any]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `org_id` | `str` | Organisation scoping the call. |
| `session_id` | `str` | Active session UUID. |
| `effect_id` | `str` | Effect identifier from the intercept response. |
| `result` | `dict[str, Any]` | The tool execution result. |

| Returns |
|---------|
| `dict[str, Any]` Always the empty dict: the proxy commits inline. |

| Exception | Condition |
|-----------|-----------|
| `AuthenticationError` | Proxy rejected the credentials (401/403). |
| `ServerError` | Proxy-side failure (5xx). |
| `TimeoutError` | Request timed out. |
| `ConnectionError` | Could not reach the proxy. |

No-op; commit is handled inline by the proxy during the tool call. Kept for API compatibility.

---

#### `fail`

```text
async def fail(
    self,
    org_id: str,
    session_id: str,
    effect_id: str,
    error: str,
) -> dict[str, Any]
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `org_id` | `str` | Organisation scoping the call. |
| `session_id` | `str` | Active session UUID. |
| `effect_id` | `str` | Effect identifier from the intercept response. |
| `error` | `str` | Human-readable error description. |

| Returns |
|---------|
| `dict[str, Any]` Always the empty dict: the proxy handles failure inline. |

| Exception | Condition |
|-----------|-----------|
| `AuthenticationError` | Proxy rejected the credentials (401/403). |
| `ServerError` | Proxy-side failure (5xx). |
| `TimeoutError` | Request timed out. |
| `ConnectionError` | Could not reach the proxy. |

No-op; fail is handled inline by the proxy during the tool call. Kept for API compatibility.

---

#### `aclose`

```text
async def aclose(self) -> None
```

Close the underlying HTTP client session.

| Returns |
|---------|
| `None` |

---

## `InterceptResponse`

**Module:** `undolog_sdk.client`

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `outcome` | `str` | One of `"Execute"`, `"Replay"`, `"AwaitingApproval"`. |
| `effect_id` | `str \| None` | Effect log entry identifier. Present for all outcomes. |
| `approval_id` | `str \| None` | Approval request identifier. Present only for `AwaitingApproval`. |
| `cached_result` | `dict \| None` | Cached tool result. Present only for `Replay`. |

**Field population by outcome:**

| Field | Execute | Replay | AwaitingApproval |
|-------|---------|--------|------------------|
| `effect_id` | ✓ | ✓ | ✓ |
| `approval_id` |, |, | ✓ |
| `cached_result` |, | ✓ |, |

---

## `UndoLogSession`

**Module:** `undolog_sdk.session`

Async context manager for an UndoLog session. Source of truth for organisation identity, session identity, and step ordering.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `org_id` | `str` | (required) | Organisation identifier that scopes all intercepted tool calls. |
| `session_id` | `str` | `uuid.uuid4()` | UUID v4 string generated once per session. |
| `_step_index` | `int` | `0` | Internal step counter. Incremented by `next_step()`. Not repr. |

### Methods

#### `__aenter__`

```text
async def __aenter__(self) -> UndoLogSession
```

| Returns |
|---------|
| `UndoLogSession` |

---

#### `__aexit__`

```text
async def __aexit__(
    self,
    exc_type: type[BaseException] | None,
    exc_val: BaseException | None,
    exc_tb: object | None,
) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `exc_type` | `type[BaseException] \| None` | Exception type if raised. |
| `exc_val` | `BaseException \| None` | Exception value if raised. |
| `exc_tb` | `object \| None` | Traceback if raised. |

| Returns |
|---------|
| `None` |

---

#### `next_step`

```text
def next_step(self) -> int
```

Advance and return the next step index. First call returns `1`, then `2`, `3`, …

| Returns |
|---------|
| `int` | The next 1-based step index. |

---

### Example

```python
async with UndoLogSession(org_id="org-abc") as session:
    result = await some_tool(arg=1, _session=session)
```

---

## `@undolog_tool`

**Module:** `undolog_sdk.decorators`

Decorator that wraps an async function with UndoLog interception.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tier` | `ToolTier` | (required) | The tool's classification (Safe, Compensable, or Irreversible). |
| `compensation` | `CompensationDescriptor \| None` | `None` | Required if `tier` is `Compensable`. |
| `client` | `UndoLogClient \| None` | `None` | A client instance. If omitted, a module-level default client is used (lazily initialised from environment). |
| `session_param` | `str` | `"_session"` | Name of the keyword argument that receives the `UndoLogSession` at call time. |

### Raises

| Exception | Condition |
|-----------|-----------|
| `ValueError` | `Compensable` tier used without a compensation descriptor. |
| `RuntimeError` | Required session parameter missing from decorated function's keyword arguments. |
| `AwaitingApprovalError` | Intercept outcome requires human approval and execution is suspended. |

### Flow

1. SAFE: execute immediately, bypass proxy entirely.
2. COMPENSABLE / IRREVERSIBLE: compute `call_signature`, call `UndoLogClient.intercept(...)`, branch on outcome.

### Outcomes

| Outcome | Behaviour |
|---------|-----------|
| `Execute` | Run wrapped function, then call `commit` or `fail` depending on success/failure. |
| `Replay` | Return cached value without entering the function body. |
| `AwaitingApproval` | Raise `AwaitingApprovalError`. Function body is not executed. |

### Example

```python
@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    return f"results for {query}"

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_send_email"),
)
async def send_email(to: str, subject: str) -> dict:
    return {"status": "sent"}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_database(db_name: str) -> dict:
    return {"status": "deleted"}
```

---

## `ToolTier`

**Module:** `undolog_sdk.tier`

Enumeration of tool tier classifications.

### Members

| Member | Value | Description | Examples |
|--------|-------|-------------|---------|
| `SAFE` | `"safe"` | Read-only or idempotent. Execute freely; no effect log entry required. | `search_web`, `read_file`, `get_user` |
| `COMPENSABLE` | `"compensable"` | Write operation with a well-defined compensation (undo). | `send_email`, `transfer_funds`, `create_record` |
| `IRREVERSIBLE` | `"irreversible"` | Cannot be undone. Requires explicit human approval before execution. | `delete_database`, `publish_to_production`, `wire_large_amount` |

### Properties

| Property | Return Type | Description |
|----------|-------------|-------------|
| `is_safe` | `bool` | `True` when the tool is SAFE. |
| `is_compensable` | `bool` | `True` when the tool is COMPENSABLE. |
| `requires_approval` | `bool` | `True` when the tool is IRREVERSIBLE. |

---

## `CompensationDescriptor`

**Module:** `undolog_sdk.tier`

Describes the compensation function to invoke when rolling back a `Compensable` tool call.

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fn_name` | `str` | (required) | Logical name matching the compensation registry. |
| `fn_version` | `str` | `"1.0.0"` | Semver version of the compensation function. |
| `args` | `dict[str, Any]` | `{}` | Arguments captured from the original call before execution. |
| `max_retries` | `int` | `3` | Max retry attempts before escalating to `compensation_failed`. |
| `retry_backoff_ms` | `int` | `1000` | Backoff delay between retries in milliseconds. |

### Class Methods

#### `new`

```text
@classmethod
def new(cls, fn_name: str, args: dict[str, Any] | None = None) -> CompensationDescriptor
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fn_name` | `str` | (required) | Logical name matching the compensation registry. |
| `args` | `dict[str, Any] \| None` | `None` | Arguments captured from the original call. |

| Returns |
|---------|
| `CompensationDescriptor` | A descriptor with default version and retry settings. |

---

## `AwaitingApprovalError`

**Module:** `undolog_sdk.decorators`

Raised when an Irreversible tool call requires human approval.

**Base class:** `RuntimeError`

### Constructor

```text
def __init__(
    self,
    approval_id: str,
    tool_name: str = "",
    step_index: int = 0,
) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `approval_id` | `str` | Approval request identifier. |
| `tool_name` | `str` | Logical name of the tool being approved. |
| `step_index` | `int` | Step index of the tool call. |

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `approval_id` | `str` | Approval request identifier. |
| `tool_name` | `str` | Logical name of the tool being approved. |
| `step_index` | `int` | Step index of the tool call. |

---

## Compensation testing harness

**Module:** `undolog_sdk.test_harness`

In-process simulation of the compensation machinery: run one compensation
function or a whole LIFO rollback under pytest with no proxy, no engine, and
no database. The names below are re-exported from `undolog_sdk`. Both
functions carry the `test_` prefix the public API mandates and set
`__test__ = False`, so pytest collects the tests written around them, not
the harness itself.

### `test_compensation`

```text
async def test_compensation(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    retries: int = 1,
    **kwargs: Any,
) -> CompensationReport
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fn` | `Callable[..., Coroutine[Any, Any, Any]]` | (required) | The async compensation function under test. |
| `*args` | `Any` | | Positional arguments for the compensation call. |
| `retries` | `int` | `1` | Extra attempts allowed beyond the first call. |
| `**kwargs` | `Any` | | Keyword arguments for the compensation call. |

Executes `fn`, then verifies the idempotency contract by invoking it a
second time with identical arguments: with `retries > 0` both results must
agree for `final_state` to be `"compensated"`, which is the behaviour the
engine relies on when it retries a compensation.

| Returns | Description |
|---------|-------------|
| `CompensationReport` | Execution counts, final state, and the result value. |

### `test_saga`

```text
async def test_saga(
    session_effects: Sequence[EffectDescriptor],
) -> SagaReport
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_effects` | `Sequence[EffectDescriptor]` | Recorded effects to roll back, in any order. |

Rolls the effects back the way the engine's compensation pass would: LIFO
by `stack_position`, a per-entry retry budget, and a stop at the first
entry that fails permanently.

| Returns | Description |
|---------|-------------|
| `SagaReport` | Per-step outcomes and the compensation invocation order. |

| Exception | Condition |
|-----------|-----------|
| `ValueError` | Two entries share a `stack_position`, or the sequence is empty. |

### `EffectDescriptor`

One recorded effect in a simulated rollback, mirroring the fields of the
engine's undo-stack entry.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tool_name` | `str` | (required) | Logical name of the tool that produced the effect. |
| `compensation` | `Callable[..., Coroutine[Any, Any, Any]]` | (required) | The async compensation callable to invoke. |
| `stack_position` | `int` | (required) | LIFO position; higher values roll back first. |
| `max_retries` | `int` | `0` | Retry budget for the entry. `0` selects the default of 3, matching the engine contract. |
| `args` | `tuple[Any, ...]` | `()` | Positional arguments for the compensation call. |
| `kwargs` | `dict[str, Any] \| None` | `None` | Keyword arguments for the compensation call, normalized to `{}` at construction. |

A negative `stack_position` raises `ValueError` at construction.

### Report types

`CompensationReport` fields:

| Field | Type | Description |
|-------|------|-------------|
| `fn_name` | `str` | Name of the compensation function under test. |
| `execution_count` | `int` | Invocations that returned normally. |
| `retry_count` | `int` | Attempts consumed beyond the initial call. |
| `final_state` | `str` | `"compensated"` on success; `"failed"` when every attempt raised or the two idempotency runs disagreed. |
| `result` | `Any` | The return value of the successful attempt. |
| `error` | `str \| None` | The last exception or mismatch message when `final_state == "failed"`. |

`SagaReport` fields:

| Field | Type | Description |
|-------|------|-------------|
| `session_result` | `str` | `"compensated"` when every entry compensated, `"halted"` when one failed permanently. |
| `steps` | `tuple[StepResult, ...]` | Per-entry outcomes in rollback execution order. |
| `compensation_order` | `tuple[str, ...]` | Tool names that compensated successfully, in invocation order. |

`StepResult` fields:

| Field | Type | Description |
|-------|------|-------------|
| `tool_name` | `str` | Logical name of the compensated tool. |
| `stack_position` | `int` | LIFO position the entry rolled back at. |
| `attempts` | `int` | Times the compensation was invoked. |
| `state` | `str` | `"compensated"` or `"failed"`. |
| `error` | `str \| None` | The last exception message when `state == "failed"`. |

### Harness classes

`CompensationTestHarness(fn)` wraps a single compensation function and
records `call_count`, `calls`, `results`, and `errors`. Its methods:
`execute(*args, **kwargs)`, `execute_with_retry(*args, **kwargs)`,
`assert_called_once()`, `assert_called_with(*args, **kwargs)`,
`assert_idempotent(*args, **kwargs)`, and `reset()`.

`SagaTestHarness(session_effects)` exposes `session_effects`,
`async rollback() -> SagaReport`, and `report()`, which returns the most
recent rollback's report or raises `RuntimeError` before one has run.

### Example

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

Both tests need only `pip install undolog-sdk[test]`, so they run in any
CI job that runs pytest.

---

## `wrap_crewai`

**Module:** `undolog_sdk.integrations`

Runs a CrewAI crew's `kickoff_async` inside an `UndoLogSession`, so the tool calls made through the crew's agents reach the proxy.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `crew` | `Any` | (required) | A `Crew`: anything exposing `kickoff_async` and `agents`. |
| `org_id` | `str \| None` | `None` | Organisation for new sessions. Falls back to `UNDOLOG_ORG_ID`, then `org_demo`. |
| `tiers` | `dict[str, ToolTier]` | `None` | Tier overrides keyed by tool name. Tools not listed default to `COMPENSABLE`. |
| `compensations` | `dict[str, str]` | `None` | Compensation registry names keyed by tool name. |
| `client` | `UndoLogClient \| None` | `None` | Explicit client. Falls back to the module-level default. |

### Returns

A `WrappedCrew` facade. The original crew is not replaced, and attributes the facade does not define are delegated to it.

### Facade properties

| Property | Type | Description |
|----------|------|-------------|
| `org_id` | `str` | Organisation that new sessions use. |
| `session_id` | `str \| None` | Session id of the most recent run, or `None` before the first. |
| `step_index` | `int` | Step progress reached by the most recent run. |
| `awaiting_approval` | `bool` | `True` while a tool waits for a human decision. |
| `approval_request` | `dict \| None` | `approval_id`, `tool_name`, and `step_index` of the pending approval, or `None`. |

### Behaviour

- Every agent's tools are instrumented at kickoff, and no agent is reassigned until every tool on every agent has been accepted: a tool the wrapper rejects leaves the crew untouched.
- The wrap is idempotent, so a second kickoff neither re-wraps nor journals a call twice.
- `kickoff_async` consumes `session_id` and `undolog_step_index` from `inputs` and passes the remaining keys to the crew, so UndoLog bookkeeping never reaches a task placeholder.
- Resuming with `session_id` alone restarts the step counter, so a retried run's calls land on the steps they already journaled and the engine replays them. Adding `undolog_step_index` continues the counter past them instead.
- Only `kickoff_async` opens a session and wraps the agents' tools. A synchronous `kickoff` gets neither, so a decorated tool raises `RuntimeError` for the missing session.
- Tools must be async: `undolog_tool` awaits the function it wraps, so a sync tool raises `ValueError` instead of failing at its first call.
- An Irreversible tool raises `AwaitingApprovalError` out of `kickoff_async`, and the facade records the run before it propagates, so the handler can read `session_id` and `approval_request`.
- Tools already decorated with `@undolog_tool` come back untouched, so a pre-decorated crew needs no `tiers` or `compensations`.

### Example

```python
from undolog_sdk import AwaitingApprovalError
from undolog_sdk.integrations import wrap_crewai

crew = wrap_crewai(build_crew(), org_id="org_demo")
try:
    result = await crew.kickoff_async()
except AwaitingApprovalError as exc:
    print(f"Approve via POST /approvals/{exc.approval_id}/approve")
    # Resuming with the session id alone repeats the step positions, so
    # completed calls replay instead of running again.
    result = await crew.kickoff_async(inputs={"session_id": crew.session_id})
```

---

## `wrap_langgraph`

**Module:** `undolog_sdk.integrations`

Runs a compiled LangGraph graph's `ainvoke` inside an `UndoLogSession`, so the tool calls made through `wrap_tools` reach the proxy.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `graph` | `Any` | (required) | A compiled LangGraph app: anything exposing `ainvoke`. |
| `org_id` | `str \| None` | `None` | Organisation for new sessions. Falls back to `UNDOLOG_ORG_ID`, then `org_demo`. |

### Returns

A `WrappedGraph` facade. The original graph is not mutated, and attributes the facade does not define are delegated to it.

### Returned state keys

| Key | Type | Description |
|-----|------|-------------|
| `session_id` | `str` | Session identity to pass into the next invocation. |
| `undolog_step_index` | `int` | Position this invocation started from. Passing it back unchanged makes the next invocation reproduce the same steps, so the engine replays them. |
| `awaiting_approval` | `bool` | `True` while a tool waits for a human decision. |
| `approval_request` | `dict \| None` | `approval_id`, `tool_name`, and `step_index` of the pending approval, or `None`. |

### Behaviour

- Instrument the tools first with `wrap_tools` and build the graph from the list it returns: a compiled graph already holds its own references to the callables.
- Only `ainvoke` opens a session. `astream` needs an `UndoLogSession` and `run_with_session` opened around the call.
- Re-invoking with the returned state resumes the same session and restarts it at the position it previously started from, so the engine replays the steps that already completed instead of running them again.
- `app.org_id` reports the organisation that new sessions use.

### Example

```python
from undolog_sdk.integrations import wrap_langgraph

app = wrap_langgraph(build_graph(), org_id="org_demo")
result = await app.ainvoke({"query": "urgent payment needed"})
if result["awaiting_approval"]:
    # POST /approvals/{approval_id}/approve, then invoke again.
    result = await app.ainvoke(result)
```

---

## `wrap_llamaindex`

**Module:** `undolog_sdk.integrations`

Runs a LlamaIndex query inside an `UndoLogSession`, so the tool calls made through the index reach the proxy.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `index` | `Any` | (required) | Anything exposing `aquery` and `tools`, such as an agent built from `FunctionTool` objects. |
| `org_id` | `str \| None` | `None` | Organisation for new sessions. Falls back to `UNDOLOG_ORG_ID`, then `org_demo`. |
| `tiers` | `dict[str, ToolTier]` | `None` | Tier overrides keyed by tool name. Tools not listed default to `COMPENSABLE`. |
| `compensations` | `dict[str, str]` | `None` | Compensation registry names keyed by tool name. |
| `client` | `UndoLogClient \| None` | `None` | Explicit client. Falls back to the module-level default. |

### Returns

A `WrappedIndex` facade. The original index is not replaced, and attributes the facade does not define are delegated to it.

### Facade properties

| Property | Type | Description |
|----------|------|-------------|
| `org_id` | `str` | Organisation that new sessions use. |
| `session_id` | `str \| None` | Session id of the most recent run, or `None` before the first. |
| `step_index` | `int` | Step progress reached by the most recent run. |
| `awaiting_approval` | `bool` | `True` while a tool waits for a human decision. |
| `approval_request` | `dict \| None` | `approval_id`, `tool_name`, and `step_index` of the pending approval, or `None`. |

### Behaviour

- The index's tools are instrumented before each query, and the list is not reassigned until every tool has been accepted: a tool the wrapper rejects leaves the index untouched.
- The wrap is idempotent, so a second query neither re-wraps nor journals a call twice.
- `aquery` consumes `session_id` and `undolog_step_index` from its keyword arguments and forwards the rest to the index, so UndoLog bookkeeping never reaches the index as a query option.
- Resuming with `session_id` alone restarts the step counter, so a retried run's calls land on the steps they already journaled and the engine replays them. Adding `undolog_step_index` continues the counter past them instead.
- Only `aquery` opens a session and wraps the tools. The other async entry points (`achat`, `astream`, `arun`) get neither, so a decorated tool raises `RuntimeError` for the missing session.
- An index that does not expose `tools` raises `AttributeError` before the session opens, rather than querying with tools the wrapper never reached.
- Tools must be async: `undolog_tool` awaits the function it wraps, so a sync tool raises `ValueError` instead of failing at its first call.
- An Irreversible tool raises `AwaitingApprovalError` out of `aquery`, and the facade records the run before it propagates, so the handler can read `session_id` and `approval_request`.
- Tools already decorated with `@undolog_tool` come back untouched, so a pre-decorated index needs no `tiers` or `compensations`.

### Example

```python
from undolog_sdk import AwaitingApprovalError
from undolog_sdk.integrations import wrap_llamaindex

index = wrap_llamaindex(build_agent(), org_id="org_demo")
try:
    result = await index.aquery("summarize the incident")
except AwaitingApprovalError as exc:
    print(f"Approve via POST /approvals/{exc.approval_id}/approve")
    # Resuming with the session id alone repeats the step positions, so
    # completed calls replay instead of running again.
    result = await index.aquery("summarize the incident", session_id=index.session_id)
```

---

## `wrap_semantic_kernel`

**Module:** `undolog_sdk.integrations`

Registers Semantic Kernel plugin functions that reach the proxy and runs each kernel invocation inside an `UndoLogSession`.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `kernel` | `Any` | (required) | A `Kernel`: anything exposing `add_function`, `invoke`, and `invoke_prompt`. |
| `org_id` | `str \| None` | `None` | Organisation for new sessions. Falls back to `UNDOLOG_ORG_ID`, then `org_demo`. |
| `tiers` | `dict[str, ToolTier]` | `None` | Tier overrides keyed by function name. Functions not listed default to `COMPENSABLE`. |
| `compensations` | `dict[str, str]` | `None` | Compensation registry names keyed by function name. |
| `client` | `UndoLogClient \| None` | `None` | Explicit client. Falls back to the module-level default. |

### Returns

A `WrappedKernel` facade. The original kernel is not replaced, and attributes the facade does not define are delegated to it.

### Facade properties

| Property | Type | Description |
|----------|------|-------------|
| `org_id` | `str` | Organisation that new sessions use. |
| `session_id` | `str \| None` | Session id of the most recent invocation, or `None` before the first. |
| `step_index` | `int` | Step progress reached by the most recent invocation. |
| `awaiting_approval` | `bool` | `True` while a function waits for a human decision. |
| `approval_request` | `dict \| None` | `approval_id`, `tool_name`, and `step_index` of the pending approval, or `None`. |

### Facade methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `add_function` | `(plugin_name: str, function_name: str, func: Any, **kwargs) -> Any` | Instruments `func` and registers it. Returns what the kernel's `add_function` returns. |
| `add_functions` | `(plugin_name: str, functions: Mapping[str, Any], **kwargs) -> None` | Instruments every function, then registers them. One rejected function registers none. |
| `invoke` | `(*args, **kwargs) -> Any` | Runs a plugin function inside a session. |
| `invoke_prompt` | `(*args, **kwargs) -> Any` | Runs a prompt function inside a session. Same session, resume-key, and approval behaviour as `invoke`. |
| `session` | `(session_id: Any = None, start_step: int = 0) -> AsyncIterator[UndoLogSession]` | Context manager opening a session for an entry point the facade does not wrap, such as an agent run. Takes the same resume arguments as `invoke`. |

### Behaviour

- Functions are instrumented at registration rather than at invocation, because a registered function keeps its own reference to the callable it wraps. Register through the facade, not the kernel: a function the kernel already holds cannot be instrumented afterwards.
- Registration is idempotent, so a function already decorated with `@undolog_tool` is registered unchanged, keeping the tier chosen at decoration time.
- `add_functions` accepts every function before registering any, so one rejected function leaves the kernel exactly as it was.
- `invoke` and `invoke_prompt` consume `session_id` and `undolog_step_index` from the arguments mapping and remove them before the kernel reads it, so UndoLog bookkeeping never reaches a function argument.
- `invoke` and `invoke_prompt` open a session per call. An agent invokes the kernel on its own schedule, so run it inside `session()`, which publishes the same session to the context variable the registered functions read. All three resume a journal the same way, and a fresh session on the next run is what the default gives you.
- Registration forwards to the kernel's `add_function` as the keyword arguments `plugin_name`, `function_name`, and `func`, plus anything the caller passed. A kernel version that names them differently raises `TypeError` at registration, before it holds the function.
- Resuming with `session_id` alone restarts the step counter, so a retried run's calls land on the steps they already journaled and the engine replays them. Adding `undolog_step_index` (or `start_step` for `session()`) continues the counter past them instead. A retry only replays if it makes the same calls in the same order.
- Functions must be named async callables: `undolog_tool` awaits the function it wraps, so a sync function raises `ValueError` at registration instead of failing at its first call. A container exposing its callable as `coroutine` is rejected rather than unwrapped.
- An Irreversible function raises `AwaitingApprovalError` out of `invoke`, `invoke_prompt`, or a `session()` block, and the facade records the run before it propagates, so the handler can read `session_id` and `approval_request`.

### Example

```python
from undolog_sdk import AwaitingApprovalError
from undolog_sdk.integrations import wrap_semantic_kernel

kernel = wrap_semantic_kernel(kernel, org_id="org_demo")
kernel.add_functions("docs", {"search": search_documents, "archive": archive_project})
try:
    result = await kernel.invoke(function=archive_project, arguments={"project_id": "proj_42"})
except AwaitingApprovalError as exc:
    print(f"Approve via POST /approvals/{exc.approval_id}/approve")
    # Resuming with the session id alone repeats the step positions, so
    # completed calls replay instead of running again.
    result = await kernel.invoke(
        function=archive_project,
        arguments={"project_id": "proj_42", "session_id": kernel.session_id},
    )
```

---

## `wrap_tools`

**Module:** `undolog_sdk.integrations`

Instruments a list of tool objects so every call through them journals. `wrap_tool` applies the same rules to a single tool.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tools` | `Sequence[Any]` | (required) | Async callables, or objects exposing an async `coroutine` attribute (pydantic `StructuredTool` style). |
| `tiers` | `dict[str, ToolTier] \| None` | `None` | Per-tool tier overrides keyed by tool name. Unlisted tools default to `COMPENSABLE`. |
| `compensations` | `dict[str, str] \| None` | `None` | Compensation registry names keyed by tool name. |
| `client` | `UndoLogClient \| None` | `None` | Client instance given to every wrapped tool. |

### Returns

A new list. Structured-tool containers are shallow-copied, so the caller's objects are never mutated. Tools already decorated with `@undolog_tool` come back unchanged, keeping their decoration-time tier.

### Raises

| Exception | Condition |
|-----------|-----------|
| `ValueError` | A `Compensable` tool has no entry in `compensations`. |
| `ValueError` | A tool is not a callable that carries a name. |

### Example

```python
from undolog_sdk.integrations import wrap_tools

tools = wrap_tools(
    [search_web, create_user, payout],
    tiers={"search_web": ToolTier.SAFE, "payout": ToolTier.IRREVERSIBLE},
    compensations={"create_user": "undo_create_user"},
)
```

---

## `call_signature`

**Module:** `undolog_sdk.signature`

```text
def call_signature(
    session_id: str,
    step_index: int,
    tool_name: str,
    args: Any,
) -> str
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | `str` | UUID string identifying the session. |
| `step_index` | `int` | Monotonically increasing step counter within the session. |
| `tool_name` | `str` | Logical name of the tool being called. |
| `args` | `Any` | JSON-compatible object (dict, list, etc.) representing the tool arguments. Canonicalized before hashing. |

### Returns

| Type | Description |
|------|-------------|
| `str` | 64-character lowercase hex string (BLAKE3-256). |

### Raises

| Exception | Condition |
|-----------|-----------|
| `ValueError` | `session_id` is not a valid UUID. |

### Byte stream (BLAKE3 input)

```
[session_id: 16 bytes]
[step_index: 4 bytes LE]
[tool_name_len: 4 bytes LE][tool_name: N bytes UTF-8]
[canonical_args_len: 4 bytes LE][canonical_args: M bytes UTF-8]
```

---

## `canonical_json`

**Module:** `undolog_sdk.signature`

```text
def canonical_json(value: Any) -> str
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `value` | `Any` | A JSON-compatible Python object (dict, list, str, int, float, bool, None). |

### Returns

| Type | Description |
|------|-------------|
| `str` | Compact JSON string with recursively sorted keys, no whitespace, matching Rust `serde_json` output for leaf values. |
