---
title: "MCP-Native Design"
description: "## The Problem"
section: "explanation"
---
# MCP-Native Design

## The Problem

A team runs agents on three frameworks: LangGraph for customer support,
CrewAI for research pipelines, and Semantic Kernel for internal
automation. Each framework has its own safety plugin ecosystem. The team
builds a LangGraph safety plugin that intercepts tool calls and enforces
approval gates. Then they build a CrewAI plugin that does the same thing.
Then a Semantic Kernel plugin. Three codebases. Three bug trackers. Three
sets of deployment pipelines. Each plugin drifts as frameworks evolve.

This pattern repeats across teams adopting AI agents. Every framework
invents its own interception API, its own middleware stack, and its own
state management. Safety is implemented N times, once per framework, and
each implementation is incompatible with the others. Teams that use
multiple frameworks either duplicate effort or pick one framework and
lock in.

The root cause is that safety is implemented at the framework layer .
inside LangGraph's node middleware, CrewAI's tool decorators, Semantic
Kernel's filter pipeline. A safety system built at this layer is tightly
coupled to the framework's internals. Switching frameworks means
rewriting the safety layer.

## The Naive Approach

Build a LangGraph safety plugin first, then port it to CrewAI, then to
Semantic Kernel. Each port is a rewrite because the interception APIs
differ:

| Framework | Interception Mechanism |
|---|---|
| LangGraph | `NodeInterrupt` and custom checkpointer |
| CrewAI | Custom tool decorator |
| Semantic Kernel | `IFunctionFilter` |
| OpenAI Agents SDK | `Runner` middleware hooks |

The LangGraph plugin hooks into the graph's checkpointer to record tool
calls. The CrewAI plugin wraps tool definitions with a decorator that
logs before and after. The Semantic Kernel plugin implements
`IFunctionFilter` on the kernel. Three different interfaces for the same
logic. When the effect log schema changes, all three must be updated.

Some teams build a shared library called by all three plugins. This
reduces duplication in the core logic but still requires three plugin
adapters. Each adapter is a maintenance liability that must track the
framework's release cycle.

## The UndoLog Approach: Operate at the MCP Protocol Layer

UndoLog does not integrate with agent frameworks. It operates at the
Model Context Protocol (MCP) layer: one level below the framework and
one level above the transport. Any framework that can make an HTTP call
can use UndoLog without framework-specific code.

### Architecture

```
Agent Framework (LangGraph / CrewAI / Semantic Kernel / …)
    │  HTTP POST /mcp/tool_call      ◄── Standard MCP transport
    ▼
┌─────────────────────────────────┐
│  Go MCP Proxy (undolog-proxy)   │  ← HTTP ingress, canonical JSON,
│                                 │    SSE dashboard events,
│  intercept → engine → execute   │    approval routing
└──────────┬──────────────────────┘
           │ gRPC (protobuf)
           ▼
┌─────────────────────────────────┐
│  Rust Effect Engine             │  ← Core safety logic,
│                                 │    tier dispatch, saga
│  undolog-engine                 │    orchestration, advisory
│  undolog-saga                   │    locks, effect log
│  undolog-store                  │
└──────────┬──────────────────────┘
           │ SQL (sqlx)
           ▼
┌─────────────────────────────────┐
│  PostgreSQL                     │  ← undolog_effect_log,
│                                 │    undolog_undo_stack,
│                                 │    undolog_approval_requests
└─────────────────────────────────┘
```

The Go proxy speaks HTTP/SSE on the ingress side and gRPC on the egress
side. The Rust engine is stateless at the HTTP layer: all state lives in
PostgreSQL. This means the engine can be scaled horizontally behind the
proxy without coordination beyond the advisory lock on PostgreSQL.

### The Protocol Boundary

The MCP protocol defines a `tools/call` request with `name` and
`arguments`. UndoLog extends this with three UndoLog-specific headers:

- `X-UndoLog-Org-Id`: tenant identifier (used for RLS in PostgreSQL)
- `X-UndoLog-Session-Id`: session identifier (correlates calls within a
  workflow)
- `Idempotency-Key`: optional (bypassed in favor of BLAKE3 signatures)

The proxy at `POST /mcp/tool_call` receives the standard MCP tool call
format, enriches it with tenant context from the headers, computes the
canonical JSON and BLAKE3 signature (matching the Rust algorithm), and
forwards the `protocol.ToolCall` struct to the Rust engine via gRPC. The
engine returns an `InterceptResponse` with one of three outcomes:
`Execute`, `Replay`, or `AwaitingApproval`. The proxy then either
forwards the call to the upstream MCP tool server, returns a cached
result, or returns a `202 Accepted` with an approval identifier.

No framework code touches this path. LangGraph, CrewAI, Semantic Kernel,
and the OpenAI Agents SDK all talk to the same HTTP endpoint. The only
framework-specific code is the equivalent of:

```python
response = httpx.post("http://undolog-proxy:8080/mcp/tool_call", json={...})
```

### The Python SDK: Decoration, Not Framework Integration

The Python SDK (`undolog_sdk`) does not depend on LangGraph or any other
framework. It provides a decorator `@undolog_tool` that wraps an async
function with the intercept-commit/replay/fail lifecycle:

```python
@undolog_tool(tier=ToolTier.COMPENSABLE, compensation=...)
async def transfer_funds(to: str, amount: Decimal) -> dict:
    return await banking_api.transfer(to, amount)
```

The decorator intercepts the function call, sends the tool name and args
to the UndoLog proxy, and branches on the outcome:

- **Execute**: calls the original function, then sends `commit` or `fail`
- **Replay**: returns the cached result without calling the function
- **AwaitingApproval**: raises `AwaitingApprovalError`

No framework hooks. No middleware chain. The same decorator works in a
LangGraph agent, a raw `asyncio` script, or a FastAPI endpoint.

The session is managed by `UndoLogSession`, an async context manager that
generates a UUID session ID and tracks step indices:

```python
async with UndoLogSession(org_id="org-abc") as session:
    result = await transfer_funds("bob", Decimal("100"), _session=session)
```

### Cross-Language Lock Agreement

The MCP-native design requires multiple languages to agree on critical
algorithms. Three such algorithms must produce identical output across
Rust, Go, and Python:

| Algorithm | Rust | Go | Python |
|---|---|---|---|
| Canonical JSON (sorted keys) | `canonical_json` in `undolog-types/src/effect.rs:81-103` | `writeCanonicalJSON` in `internal/proxy/signature.go:33-72` | `canonical_json` in `undolog_sdk/signature.py:19-42` |
| BLAKE3 call signature | `CallSignature::compute` in `undolog-types/src/effect.rs:33-59` | (delegated to Rust engine) | `call_signature` in `undolog_sdk/signature.py:45-109` |
| FNV-1a advisory lock key | `advisory_lock_key` in `undolog-store/src/effect_store.rs:585-602` | (engine-owned) | (not needed. Python sends to proxy) |

Each implementation includes unit tests that verify cross-language
equivalence. The canonical JSON tests sort keys recursively and strip
whitespace. The signature tests verify the same 64-char hex output.

### Why Two Languages (Go + Rust)?

The Go proxy handles HTTP, SSE, and upstream tool execution. I/O-heavy
work that Go's goroutine model handles efficiently. The Rust engine
handles transactional database access, concurrency control (advisory
locks), and the effect state machine: correctness-critical work that
Rust's type system and ownership model make easier to reason about.

The gRPC boundary between them enforces a clean separation of concerns.
The proxy cannot bypass the engine's safety decisions because it has no
direct database access. The engine cannot leak into HTTP concerns because
it exposes only a protobuf service.

### SSE Dashboard Events

The proxy broadcasts real-time events over Server-Sent Events (SSE) at
`GET /events`. Each intercepted call, commit, replay, and approval action
generates a typed event:

| Event Type | When | Payload |
|---|---|---|
| `effect_intercepted` | Tool call received | org, session, tool name, tier |
| `effect_committed` | Execution succeeded | effect_id, result summary |
| `effect_failed` | Execution failed | effect_id, error message |
| `effect_replayed` | Cache hit | effect_id, replay count |
| `approval_required` | Irreversible call | approval_id, reason, args |

The dashboard consumes these events via `EventSource` (browser SSE API).
No polling. No WebSocket management. The Go proxy's `sse.Broadcaster`
manages fan-out to connected clients.

## Trade-offs

**MCP-native means no framework-specific optimizations.** LangGraph's
native checkpointer can replay graph state efficiently. UndoLog cannot
use it because the proxy sits below the framework. Session snapshots in
`undolog_session_snapshots` provide a partial substitute: the Rust
engine serializes session state periodically: but it is not as tight as
a framework-native integration.

**Session state lives in UndoLog, not the framework.** The session state
machine (active → failed → compensating → compensated) is managed by the
Rust engine and stored in PostgreSQL. If the framework also manages
session state (e.g., LangGraph's thread state), there is a split-brain
risk. Teams must decide which system owns session lifecycle.

**Two-language deployment.** Running both a Go service and a Rust service
increases operational complexity: two sets of binary builds, two sets of
monitoring, two sets of runtime configurations. The gRPC boundary adds
network latency (sub-millisecond on localhost, measurable on Kubernetes).

**HTTP-only ingress.** The proxy exposes only HTTP/SSE. Frameworks that
prefer WebSocket or gRPC for MCP transport must use an adapter or an
intermediate translation layer. This is a deliberate simplification, the
proxy's surface area is intentionally small.

## Alternatives Considered

**Framework-specific plugins (LangGraph extension, CrewAI plugin, Semantic
Kernel connector).** Three codebases, three maintenance tracks. Rejected
because it does not scale to the growing ecosystem of agent frameworks.

**Unified agent middleware library.** A single library with adapters for
each framework. The shared logic is not duplicated, but each adapter must
still be maintained. Rejected because the adapter layer is still
framework-coupled and breaks on framework version bumps.

**Replace MCP with a custom protocol.** Build UndoLog's own transport
protocol instead of layering on MCP. More expressive (can carry
UndoLog-specific metadata natively) but requires frameworks to implement
a second protocol alongside MCP. Rejected because it fragments the
ecosystem.

## Further Reading

- [Safety Model](./safety-model.md): three-tier classification at the
  protocol level
- [Exactly-Once Semantics](./exactly-once-semantics.md): cross-language
  signature algorithm
- [Saga Pattern](./saga-pattern.md): compensation orchestration
- Code: `services/undolog-proxy/internal/proxy/handler.go:131-286`. MCP
  tool call handler
- Code: `services/undolog-proxy/internal/protocol/types.go:1-109` .
  shared protocol types
- Code: `services/undolog-proxy/internal/proxy/signature.go:15-31` .
  canonical JSON in Go
- Code: `sdks/undolog-py/undolog_sdk/decorators.py:51-176`: framework-
  agnostic Python decorator
- Code: `sdks/undolog-py/undolog_sdk/signature.py:45-109`. Python
  BLAKE3 implementation
