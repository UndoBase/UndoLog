---
title: "Python SDK Reference"
description: "**Package:** `undolog_sdk` **Module paths:** `undolog_sdk.client`, `undolog_sdk.session`, `undolog_sdk.decorators`, `undolog_sdk.tier`, `undolog_sdk.signature`"
section: "reference"
---
# Python SDK Reference

**Package:** `undolog_sdk`  
**Module paths:** `undolog_sdk.client`, `undolog_sdk.session`, `undolog_sdk.decorators`, `undolog_sdk.tier`, `undolog_sdk.signature`

---

## `UndoLogClient`

**Module:** `undolog_sdk.client`

Async HTTP client for the UndoLog MCP Proxy. Communicates with the Go proxy for tool call interception, commit, and fail reporting.

### Constructor

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proxy_url` | `str \| None` | `None` | Base URL of the UndoLog proxy. Falls back to `UNDOLOG_PROXY_URL` env var, then `http://localhost:8080`. |
| `http_client` | `httpx.AsyncClient \| None` | `None` | Pre-configured HTTP client. If omitted, a default client is created with `base_url=proxy_url` and `timeout=30.0`. |

### Methods

#### `intercept`

```python
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
| `httpx.HTTPStatusError` | Proxy-level HTTP errors (4xx/5xx). |
| `httpx.RequestError` | Connection or timeout errors. |

**HTTP:** `POST /api/v1/intercept` with headers `X-UndoLog-Org-Id`, `X-UndoLog-Session-Id`.

---

#### `commit`

```python
async def commit(
    self,
    org_id: str,
    session_id: str,
    effect_id: str,
    result: dict[str, Any],
) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `org_id` | `str` | Organisation scoping the call. |
| `session_id` | `str` | Active session UUID. |
| `effect_id` | `str` | Effect identifier from the intercept response. |
| `result` | `dict[str, Any]` | The tool execution result. |

| Returns |
|---------|
| `None` |

| Exception | Condition |
|-----------|-----------|
| `httpx.HTTPStatusError` | Proxy-level HTTP errors. |
| `httpx.RequestError` | Connection or timeout errors. |

**HTTP:** `POST /api/v1/commit`

---

#### `fail`

```python
async def fail(
    self,
    org_id: str,
    session_id: str,
    effect_id: str,
    error: str,
) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `org_id` | `str` | Organisation scoping the call. |
| `session_id` | `str` | Active session UUID. |
| `effect_id` | `str` | Effect identifier from the intercept response. |
| `error` | `str` | Human-readable error description. |

| Returns |
|---------|
| `None` |

| Exception | Condition |
|-----------|-----------|
| `httpx.HTTPStatusError` | Proxy-level HTTP errors. |
| `httpx.RequestError` | Connection or timeout errors. |

**HTTP:** `POST /api/v1/fail`

---

#### `aclose`

```python
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

```python
async def __aenter__(self) -> UndoLogSession
```

| Returns |
|---------|
| `UndoLogSession` |

---

#### `__aexit__`

```python
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

```python
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

```python
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

```python
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

## `call_signature`

**Module:** `undolog_sdk.signature`

```python
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

```python
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
