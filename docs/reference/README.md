---
title: "Reference"
description: "Technical reference for UndoLog APIs, protocols, and configuration."
section: "reference"
---
# Reference

Technical reference for UndoLog APIs, protocols, and configuration.

| Document | Contents |
|---|---|
| [Python SDK](api/python-sdk.md) | `UndoLogClient`, `UndoLogSession`, `@undolog_tool` decorator |
| [Go Proxy REST API](api/go-proxy-rest.md) | HTTP endpoints, request/response formats, SSE dashboard events |
| [Rust Engine gRPC API](api/rust-engine-grpc.md) | Service definitions, message types, call patterns |
| [Configuration](configuration.md) | Environment variables, proxy config, engine config |
| [Tool tiers](tool-tiers.md) | Three-tier safety model: auto, confirm, gate |
| [Call signature spec](call-signature.md) | BLAKE3 signature algorithm, canonical JSON format |
| [Effect states](effect-states.md) | State machine: pending, running, compensated, completed, failed |
| [Error codes](error-codes.md) | gRPC and HTTP error codes, meanings, recovery actions |
| [Database schema](database-schema.md) | Table definitions, indexes, advisory lock key layout |
