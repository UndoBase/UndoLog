---
title: "Tool Tiers Reference"
description: "Every tool is classified at registration time into exactly one tier. Classification is declarative (SDK annotation), never inferred by the LLM."
section: "reference"
---
# Tool Tiers Reference

Every tool is classified at registration time into exactly one tier. Classification is declarative (SDK annotation), never inferred by the LLM.

---

## `SAFE`

**Enum value:** `"safe"` (Python: `ToolTier.SAFE`, Rust: `ToolTier::Safe`)

### When to use

- Read-only operations (no side effects)
- Idempotent operations (repeated execution produces the same result)
- Query-like tools: `search_web`, `read_file`, `get_user`, `list_records`
- Tools that call internal read APIs with no mutation

### System behaviour

| Aspect | Behaviour |
|--------|-----------|
| Effect log | No entry created. Bypasses the effect log entirely. |
| Interception | Not intercepted. The function body executes immediately. |
| Compensation | Not required. No undo stack entry. |
| Approval | Not required. |
| Proxy call | Not sent to the proxy. Direct execution only. |
| Replay | Not applicable. No effect to replay. |
| Session state | Step index still incremented. |

### Example declaration

**Python:**
```python
@undolog_tool(tier=ToolTier.SAFE)
async def search_web(query: str) -> str:
    ...
```

**Rust (registry):**
```rust
ToolTier::Safe
```

---

## `COMPENSABLE`

**Enum value:** `"compensable"` (Python: `ToolTier.COMPENSABLE`, Rust: `ToolTier::Compensable { compensation }`)

### When to use

- Write operations with a well-defined compensation (undo) function
- Operations that can be reversed by calling another tool
- Examples: `send_email`, `transfer_funds`, `create_record`, `update_profile`

### System behaviour

| Aspect | Behaviour |
|--------|-----------|
| Effect log | Entry created in `undolog_effect_log` with state `Pending → Executing → Committed` on success. |
| Interception | Call sent to proxy via `UndoLogClient.intercept()`. |
| Compensation | Required. `CompensationDescriptor` must be provided. Registered in `undolog_undo_stack` **before** execution. |
| Approval | Not required. |
| Compensated on failure | Yes. The undo stack is walked LIFO when a session fails. |
| Retry | Compensation function retries up to `max_retries` with `retry_backoff_ms` backoff. |
| Replay | Possible if the same `call_signature` exists (exactly-once semantics). |

### Compensation requirements

| Requirement | Description |
|-------------|-------------|
| `fn_name` | Must match a registered function in `undolog_compensation_registry`. |
| `fn_version` | Semver of the compensation function. |
| `args` | Arguments captured from the original call **before** execution. |
| `max_retries` | Max retry attempts before escalating to `CompensationFailed`. Default `3`. |
| `retry_backoff_ms` | Base delay between retries in ms. Default `1000`. |

### Error on missing compensation

If `COMPENSABLE` is used without a `CompensationDescriptor`, `undolog_tool` raises `ValueError`.

### Example declaration

**Python:**
```python
@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("undo_send_email", {"to": "{to}"}),
)
async def send_email(to: str, subject: str) -> dict:
    ...
```

---

## `IRREVERSIBLE`

**Enum value:** `"irreversible"` (Python: `ToolTier.IRREVERSIBLE`, Rust: `ToolTier::Irreversible { reason }`)

### When to use

- Operations that cannot be undone programmatically
- Destructive operations with permanent consequences
- Operations requiring human oversight before execution
- Examples: `delete_database`, `publish_to_production`, `wire_large_amount`, `terminate_instances`

### System behaviour

| Aspect | Behaviour |
|--------|-----------|
| Effect log | Entry created in `undolog_effect_log` with state `Pending → Approved → Committed` on success. |
| Interception | Call sent to proxy via `UndoLogClient.intercept()`. |
| Compensation | Not supported. The operation is irreversible. |
| Approval | Required. Execution is suspended until a human approves or rejects via the dashboard API. |
| Session state | Session transitions to `AwaitingApproval` while pending. |
| On approval | Effect transitions to `approved`. The proxy executes the tool separately and commits the result. No engine-level `executing` state. |
| On rejection | Session transitions to `Halted`, effect transitions to `Rejected`. |
| Retry | No automatic retry. The caller must resubmit after approval resolution. |
| Replay | Not applicable for pending/approved effects. Possible if already committed. |

### Approval requirements

| Requirement | Description |
|-------------|-------------|
| `reason` | Human-readable explanation shown in the approval UI. Must be non-empty. |
| `risk_tags` | Tags categorising the risk (e.g. `["financial", "external-api"]`). |
| `estimated_impact` | Free-text description of what will happen (e.g. "debits up to $amount"). |
| `proposed_args` | The original tool arguments. Shown in the UI for review. |
| `agent_context` | Session context (last N tool calls + reasoning trace). Shown alongside the proposal. |
| `timeout_at` | Deadline for approval. Configurable per organisation. |
| `auto_approve_on_timeout` | Whether the tool auto-executes if no decision is made by `timeout_at`. |

### Error on missing reason

Rust returns `ToolNotRegistered` if reason is empty. Python SDK does not enforce at decoration time.

### Example declaration

**Python:**
```python
@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def delete_database(db_name: str) -> dict:
    ...
```

---

## Comparison Table

| Property | SAFE | COMPENSABLE | IRREVERSIBLE |
|----------|------|-------------|--------------|
| Effect log entry | No | Yes | Yes |
| Proxy interception | No | Yes | Yes |
| Compensation | Not required | Required | Not supported |
| Human approval | Not required | Not required | Required |
| Session blocks on call | No | No | Yes (until approval) |
| Can be replayed | No | Yes | Yes (after committed) |
| Can be undone | N/A (no effect) | Yes (via compensation) | No |
| Step index incremented | Yes | Yes | Yes |
| Call signature computed | No | Yes | Yes |
| Affects undo stack | No | Yes | No |
