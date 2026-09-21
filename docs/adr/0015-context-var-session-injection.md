---
title: "ADR 0015: Context-Var Session Injection for Python SDK"
description: "- **Date:** 2026-09-21 - **Status:** Proposed - **Deciders:** UndoLog Core Team"
section: "adr"
---

# ADR 0015: Context-Var Session Injection for Python SDK

- **Date:** 2026-09-21
- **Status:** Proposed
- **Deciders:** UndoLog Core Team

## Context

The Python SDK requires `_session=session` as a keyword argument in every
decorated tool call. This creates two problems:

1. **Signature pollution.** The `_session` parameter leaks UndoLog
   internals into the tool function signature. Frameworks that inspect
   function signatures (LangGraph, CrewAI, Semantic Kernel) may reject
   or mis-handle the extra parameter.

2. **Developer friction.** Every decorated call must include
   `_session=session`. Forgetting it raises `RuntimeError` at call time,
   but the error message does not suggest the context-var alternative.
   This creates a constant source of friction when writing tool
   functions.

The TypeScript SDK solved this with `AsyncLocalStorage`. Python has the
equivalent primitive: `contextvars.ContextVar`.

Current state:

- `_session` is a required keyword argument in `undolog_tool`
- No context-variable mechanism exists in the SDK
- The session is manually threaded through every tool call
- Forgetting `_session` raises `RuntimeError` at call time

## Decision

Use `contextvars.ContextVar` to propagate `UndoLogSession` through the
call stack. The `_session` parameter becomes optional: if provided
explicitly it takes precedence, otherwise the decorator falls back to
the context variable.

The public API is:

- `UndoLogContext` class holding a `ContextVar[UndoLogSession]`
- `run_with_session(session)` async context manager that sets the var
- `get_current_session()` returns the current session or `None`
- `require_current_session()` returns the session or raises
  `RuntimeError`

## Alternatives Considered

### Alternative 1: ContextVar with Explicit Fallback (Chosen)

- **Pros:** Zero boilerplate for the common case. Backward compatible
  with existing `_session=session` usage. Explicit parameter takes
  precedence over context var, allowing targeted overrides. Standard
  library (`contextvars`), no dependencies.
- **Cons:** Two code paths in the decorator (context var vs explicit
  parameter). Slightly more complex than a single required parameter.
- **Chosen?** Yes. Eliminates the developer-friction failure mode while
  remaining backward compatible.

### Alternative 2: Thread-Local Storage

- **Pros:** Familiar pattern from WSGI frameworks.
- **Cons:** Not compatible with `asyncio`. Thread locals are
  per-thread, not per-task. Multiple concurrent agent runs in the
  same thread would share state, causing race conditions.
- **Chosen?** No. Incompatible with async Python.

### Alternative 3: Function Attribute Injection

- **Pros:** Simple to implement. No new abstractions.
- **Cons:** Pollutes the function object. Not inherited by nested
  calls. Frameworks that inspect function attributes may be confused.
  Does not solve the developer-friction problem if a call bypasses
  the decorator.
- **Chosen?** No. Does not solve the core problem.

### Alternative 4: Keep Required `_session` Parameter

- **Pros:** Simplest implementation. No new concepts.
- **Cons:** Signature pollution. Developer friction. Framework
  incompatibility. This is the status quo and the problem
  being solved.
- **Chosen?** No. This is the problem statement.

## Consequences

**Positive:**

- Tool function signatures are clean: no `_session` parameter required
- Developer friction eliminated: no need to thread `_session` through calls
- Framework auto-instrumentation becomes possible (PY-3)
- Single-line integration: `async with run_with_session(session):`
- Backward compatible: existing `_session=session` still works

**Negative:**

- Two resolution paths in the decorator (context var vs explicit)
- Migration required for existing code (though not urgent)
- `contextvars` is Python 3.7+ only (not a constraint: SDK requires 3.10+)

**Risks:**

- Frameworks that create tasks with a custom empty `context` may lose
  the context var. Mitigation: document that `run_with_session` must
  wrap the entire agent loop. `asyncio.create_task()` copies the
  current context by default.
- Testing requires care: `contextvars` are task-local, not thread-local.
  Tests must use `asyncio.run` or `pytest-asyncio` to isolate context.

## References

- Python `contextvars` documentation: https://docs.python.org/3/library/contextvars.html
- TypeScript SDK session implementation: `sdks/undolog-ts/src/session.ts`
- Plan item: PY-1 in `plan/python-sdk.md`
- Analysis: section 3.9 (blocker #9), section 5.2, section 6.3, section 9.2
