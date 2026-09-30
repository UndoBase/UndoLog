---
title: "ADR 0016: Replay Shape Contract"
description: "- **Date:** 2026-09-24 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0016: Replay Shape Contract

- **Date:** 2026-09-24
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

When the Python SDK decorator intercepts a tool call, the proxy responds
with one of three outcomes: Execute, Replay, or AwaitingApproval. On
Execute, the decorator runs the tool function and returns its raw result
to the caller. On Replay, the decorator returns the cached result stored
by the proxy.

The problem is that these two paths return different shapes:

- **Execute path:** returns the raw Python value from the tool function
  (e.g., `{"id": 1, "name": "widget"}`).
- **Replay path:** returns the full `ToolResult` envelope from the proxy
  (e.g., `{"success": True, "output": {"id": 1, "name": "widget"}}`).

The `ToolResult` envelope (`{success, output, error, duration_ms}`) is a
wire-format structure defined in the Go proxy and protobuf layers. It is
an internal transport detail, not part of the SDK's public contract.
Callers should not need to know whether a result came from execution or
replay; the return shape must be identical in both cases.

Current state:

- `decorators.py` line 140 returns `response.cached_result` on Replay,
  which is the full `ToolResult` envelope.
- `decorators.py` line 197 returns `result` (the raw function value) on
  Execute.
- Tests assert the envelope shape on Replay (`{"success": True, "output": ...}`)
  and the raw shape on Execute, confirming the asymmetry.
- The TypeScript SDK avoids this by re-executing the function on replay
  rather than returning the cached envelope.

## Decision

Unwrap the `ToolResult` envelope on the Replay path so the decorator
returns the tool's natural return type in both cases. Specifically,
extract `cached_result["output"]` from the envelope before returning.

Both Execute and Replay will return the same shape: whatever the tool
function would naturally return.

## Alternatives Considered

### Alternative 1: Unwrap Envelope on Replay (Chosen)

- **Pros:** Callers get a consistent return type regardless of outcome.
  No change to the Execute path. Minimal code change (one line in
  `decorators.py`). Matches the principle of least surprise. Aligns
  with the TypeScript SDK's behavior (both return the raw result).
- **Cons:** Slightly more complex Replay path (must handle the
  `output` extraction). If the proxy ever changes the envelope shape,
  the unwrapping logic must be updated.
- **Chosen?** Yes. Consistency is a correctness issue, not a style
  preference. Callers should not branch on outcome to extract the
  result.

### Alternative 2: Document the Asymmetry

- **Pros:** No code change. Simplest to implement.
- **Cons:** Every caller must handle two return shapes. Framework
  integrations (LangGraph, CrewAI, Semantic Kernel) that inspect
  return values will break on Replay. This pushes complexity to
  every consumer instead of solving it once.
- **Chosen?** No. Documenting a bug is not a fix.

### Alternative 3: Re-Execute Function on Replay

- **Pros:** Guaranteed identical return type (same code path). The
  TypeScript SDK uses this approach.
- **Cons:** Defeats the purpose of replay. The proxy returns a cached
  result specifically to avoid re-execution (idempotency, side-effect
  safety). Re-executing could trigger duplicate side effects if the
  tool is not perfectly idempotent. Adds latency.
- **Chosen?** No. Replay exists to avoid re-execution. Unwrapping the
  envelope is the correct solution.

## Consequences

**Positive:**

- Callers receive the same return shape on Execute and Replay
- Framework integrations no longer need outcome-aware type handling
- Test assertions become simpler (one shape to verify)
- SDK contract is cleaner: `ToolResult` is a transport detail, not
  part of the public API

**Negative:**

- Existing tests that assert the envelope shape on Replay must be
  updated to assert the unwrapped shape
- The unwrapping logic must handle edge cases (missing `output` key,
  non-dict cached result)

**Risks:**

- If the proxy ever returns a `cached_result` without an `output` key,
  the unwrapping must fall back gracefully. Mitigation: use
  `cached.get("output", cached)` so non-envelope results pass through
  unchanged.
- Downstream code that depends on the envelope shape (e.g., checking
  `result["success"]`) will break. Mitigation: this is the intended
  change; such code was relying on an implementation detail.

## References

- Python SDK decorator implementation: `sdks/undolog-py/undolog_sdk/decorators.py`
- Go proxy ToolResult definition: `services/undolog-proxy/internal/protocol/types.go`
- Protobuf ToolResult message: `proto/undolog.proto`
- Plan item: PY-6 in `plan/python-sdk.md`
- TypeScript SDK replay behavior: `sdks/undolog-ts/src/decorators.ts`
