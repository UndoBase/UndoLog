---
title: "ADR 0006: MCP Protocol Layer for Framework Integration"
description: "- **Date:** 2026-02-19 - **Status:** Accepted - **Deciders:** UndoLog Core Team"
section: "adr"
---
# ADR 0006: MCP Protocol Layer for Framework Integration

- **Date:** 2026-02-19
- **Status:** Accepted
- **Deciders:** UndoLog Core Team

## Context

Tool call interception must work across multiple agent frameworks. LangGraph, CrewAI, Semantic Kernel, OpenAI Agents SDK, and others. Each framework has its own mechanism for defining and invoking tools. Writing a dedicated plugin per framework duplicates interception logic, diverges in behavior over time, and increases the maintenance burden for each new framework release.

## Decision

Intercept at the function decoration layer in the Python SDK and the proxy layer in the MCP-style REST API, not at the framework level.

## Alternatives Considered

### Framework-Specific Plugins

- **Pros:** Deep integration with each framework's native tool lifecycle, hooks, lifecycle events, and state management align with framework conventions. Potential access to framework-specific features (e.g., LangGraph's checkpointing).
- **Cons:** Requires N plugins and N regression test suites for N frameworks. Framework upgrades frequently break plugin internals. Keeping behavior consistent across all plugins is difficult, subtle differences in timing or error handling emerge.
- **Chosen?** No. The maintenance burden of N plugins outweighs the integration quality benefit.

### AST-Level Instrumentation

- **Pros:** Truly framework-independent: rewrites function definitions at the Python AST level to inject interception logic. No framework knowledge needed. Works with any code that defines functions.
- **Cons:** Extremely fragile. A minor change in how a framework decorates or wraps tool functions breaks the AST transformation. Hard to debug, the executed code differs from the source code the developer wrote. Python version upgrades can change AST node structures.
- **Chosen?** No. The brittleness and debugging difficulty make AST instrumentation unsuitable for a production reliability component.

### MCP Protocol Implementation (Full Standard)

- **Pros:** Standardized protocol for tool definition and invocation. Framework-agnostic by design. Could work with any MCP-compatible client without UndoLog's SDK.
- **Cons:** Requires all tools to be defined as MCP tools. Existing frameworks may have tens of tools that would need migration or an MCP adapter layer. MCP is still evolving as a standard, risking breakage on protocol version changes.
- **Chosen?** No. Requiring MCP compatibility from all tools is too restrictive for adoption. However, the proxy layer implements an MCP-style REST API, providing an integration path for MCP-native clients.

## Consequences

**Positive:** One SDK implementation works across all agent frameworks. Framework upgrades do not break UndoLog, as long as the framework calls the decorated function, interception works. Adding support for a new framework requires only understanding how that framework invokes tool functions, not building a new plugin.

**Negative:** Less framework-native integration: state management, session lifecycle, and approval workflows live in UndoLog rather than in the framework's native constructs (e.g., LangGraph's checkpoint system cannot be used directly as the UndoLog state source). Tool authors must explicitly decorate each tool with `@undo.tool()` rather than benefiting from automatic discovery.

**Risks:** Some frameworks may wrap tool functions in ways that bypass Python decorators (e.g., by inspecting `__wrapped__` or calling the raw function via `inspect.signature`). Each new framework version requires validation that the decoration pattern still works.
