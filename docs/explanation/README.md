---
title: "Explanation"
description: "Background and rationale for UndoLog's design decisions."
section: "explanation"
---
# Explanation

Background and rationale for UndoLog's design decisions.

| Document | What it explains |
|---|---|
| [Safety model](safety-model.md) | Why three tiers, how the approval gate works, crash guarantees |
| [Exactly-once semantics](exactly-once-semantics.md) | BLAKE3 call signatures, advisory locks, idempotency key design |
| [Saga pattern](saga-pattern.md) | LIFO compensation ordering, state machine, crash recovery protocol |
| [MCP-native design](mcp-native-design.md) | Why Model Context Protocol instead of framework-specific hooks |
| [Comparison with LangGraph](comparison-with-langraph.md) | Factual comparison of safety, idempotency, rollback, and approval features |
