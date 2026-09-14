---
title: "Deprecation Policy"
description: "How UndoLog handles feature deprecation and removal."
section: "reference"
---
# Deprecation Policy

How UndoLog handles feature deprecation and removal.

---

## Semantic versioning

UndoLog follows [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR** version changes for incompatible API changes
- **MINOR** version changes for backward-compatible functionality
- **PATCH** version changes for backward-compatible bug fixes

During 0.x development, minor version bumps may include breaking
changes. These are documented in the changelog with a migration guide.

---

## Deprecation process

When a feature is no longer recommended:

1. **Announce.** The changelog entry marks the feature as deprecated
   with a recommended replacement.

2. **Document.** A migration guide is added to the changelog or a
   dedicated migration doc.

3. **Warn.** The deprecated feature emits a runtime warning (log line
   at `warn` level) when used.

4. **Remove.** The feature is removed in the next major version (or
   the next minor version during 0.x).

---

## Deprecation timeline

| Phase | Duration | What happens |
|-------|----------|--------------|
| Deprecated | Until next major version | Feature works but emits warnings |
| Removed | Next major version | Feature no longer available |

During 0.x, deprecations may be removed in the next minor version
if the replacement is stable and the migration is straightforward.

---

## What is not deprecated

- **gRPC proto package versions.** `undolog.v1` will remain available
  even after `undolog.v2` is released. Both versions will be served
  until the next major version.

- **Environment variables.** Existing env vars are never renamed.
  New env vars may be added as aliases, but the old names continue
  to work.

- **Effect states.** The state machine transitions are part of the
  core contract. States may be added (e.g. `cancelled`) but existing
  states are never removed or renamed.

---

## See also

- [Compatibility matrix](compatibility-matrix.md): version compatibility
- [Error codes](error-codes.md): gRPC and HTTP error codes
