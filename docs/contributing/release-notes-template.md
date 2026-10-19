---
title: "Release notes template"
description: "Standard template and guidelines for authoring UndoLog release notes."
section: "contributing"
---
# Release notes template

This document provides the standard template for authoring UndoLog release notes. Release notes accompany minor and major version tags, summarizing headline changes, architectural advancements, breaking modifications, and upgrade instructions.

## Guidelines

- **Audience:** Written for application developers, infrastructure operators, and security reviewers adopting UndoLog.
- **Tone:** Clear, objective, and technical. Describe what the code does without internal jargon or hype.
- **Keep a Changelog alignment:** The sections must adhere strictly to [Keep a Changelog](https://keepachangelog.com/) category ordering:
  1. Added
  2. Changed
  3. Deprecated
  4. Removed
  5. Fixed
  6. Security
- **Cross-references:** Every major claim must link to its corresponding Architecture Decision Record in `docs/adr/`, pull request, or relevant guide in `docs/guides/`.
- **Natural punctuation:** Use colons and periods only. Never use em dashes in release notes or documentation.

---

## Template

Copy the markdown outline below when drafting release notes for a new release.

```markdown
# UndoLog vX.Y.Z Release Notes

Released: YYYY-MM-DD

## Highlights

A 2-4 paragraph narrative overview summarizing the release's major themes, architectural enhancements, and primary benefits.

- **Theme 1:** Summary of major capability and why it matters. Link relevant ADRs: [ADR 00XX](../adr/00XX-title.md).
- **Theme 2:** Summary of secondary capability or platform enhancement.

## Breaking Changes

List any breaking API, protocol, wire format, or configuration changes with clear migration steps.

- **Component:** Description of the breaking change, impact, and necessary action before upgrading.

## Added

- Feature description with link to relevant guide or ADR.
- Feature description.

## Changed

- Modification description.

## Deprecated

- Deprecation notice detailing the timeline and replacement path.

## Removed

- List of removed features or deprecated endpoints removed in this release.

## Fixed

- Description of bug fixes with root cause summary and issue links.

## Security

- Security hardening improvements or vulnerability resolutions.

## Migration Guide

Step-by-step instructions for operators and developers upgrading from the previous minor version.

### 1. Configuration Updates

Explain configuration or environment variable changes.

### 2. Database Migrations

Run database migrations:

```bash
cargo run -p undolog-migrate -- up
```

### 3. Service Upgrades

Recommended upgrade sequence (database, engine, proxy, SDK consumers).

## Contributors

Thank you to everyone who contributed code, documentation, and feedback for this release.
```
