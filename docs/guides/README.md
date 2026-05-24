---
title: "Guides"
description: "How-to guides for common UndoLog workflows."
section: "guides"
---
# Guides

How-to guides for common UndoLog workflows.

| Guide | What it covers |
|---|---|
| [Annotating tools](annotating-tools.md) | Using `@undolog_tool` to mark effects, configure tiers, and register compensations |
| [Integrating with LangGraph](integrating-langgraph.md) | Wiring `UndoLogSession` into a LangGraph agent graph |
| [Integrating with CrewAI](integrating-crewai.md) | Protecting CrewAI crews with three-tier safety and approval polling |
| [Integrating with Semantic Kernel](integrating-semantic-kernel.md) | Wrapping Semantic Kernel plugin functions with UndoLog |
| [Writing compensations](writing-compensations.md) | Best practices for idempotent, safe compensation handlers |
| [Configuring approval gates](configuring-approval-gates.md) | Setting up manual or automated approval steps before high-risk effects |
| [Deploying with Docker](deploying-with-docker.md) | Running the full stack with Docker Compose |
| [Running in production](running-in-production.md) | Advisory lock tuning, connection pooling, partition management |
