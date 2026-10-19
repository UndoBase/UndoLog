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
| [Integrating with LlamaIndex](integrating-llamaindex.md) | Running LlamaIndex tools inside UndoLog sessions with approval gating |
| [Migrating session injection](migrating-session-injection.md) | Moving from explicit `_session` threading to `run_with_session` |
| [Writing compensations](writing-compensations.md) | Best practices for idempotent, safe compensation handlers |
| [Writing proxy plugins](writing-proxy-plugins.md) | Authoring custom request interceptors, failure policies, and chain ordering |
| [Configuring approval gates](configuring-approval-gates.md) | Setting up manual or automated approval steps before high-risk effects |
| [Deploying with Docker](deploying-with-docker.md) | Running the full stack with Docker Compose |
| [Helm installation](helm-installation.md) | Deploying on Kubernetes with the Helm chart |
| [OpenTelemetry setup](opentelemetry-setup.md) | Configuring distributed tracing with OTLP and Grafana |
| [Running in production](running-in-production.md) | Advisory lock tuning, connection pooling, partition management |
| [PostgreSQL High Availability](postgresql-ha.md) | WAL streaming, read replicas, health checks, and connection pooling for production deployments |
| [Capacity planning](capacity-planning.md) | Pool sizing, replica scaling, retention and partition levers, and storage forecasting |
