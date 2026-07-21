---
title: "Contributing to UndoLog"
description: "Thanks for your interest in UndoLog. This guide walks you through how to find work, set up your environment, submit changes, and get them reviewed."
section: "contributing"
---
# Contributing to UndoLog

Thanks for your interest in UndoLog. This guide walks you through how to find work, set up your environment, submit changes, and get them reviewed.

## Finding work

Issues labelled **`good first issue`** are the best place to start. These are small, well-scoped, and include pointers to the relevant code.

Browse the issue tracker, filter by `good first issue`, and comment on any issue you want to pick up. A maintainer will assign it to you.

If you have your own idea for a change, open a feature request issue first. Discuss the approach before writing code, it saves time and avoids rejected PRs.

## Contribution workflow

1. **Fork** the repository.
2. **Create a branch** from `main`. Use the naming convention `type/short-description`, for example `feat/saga-orchestrator`, `fix/advisory-lock-empty-string`, `docs/adr-blake3-decision`.
3. **Make your changes.** Write tests, update documentation, and add a CHANGELOG entry.
4. **Commit** using [Conventional Commits](COMMIT_AND_PR_STANDARDS.md#11-format). Each commit must compile and pass tests.
5. **Push and open a PR.** The PR title must also follow Conventional Commits format, it becomes the squash-merge commit message.
6. **Respond to review feedback.** Mark `[block]` comments as resolved after addressing them.

## Development environment

You need these tools installed:

- **Docker**: for PostgreSQL and the full stack
- **Rust 1.87+**: for the engine, store, types, and saga crates
- **Go 1.22+**: for the proxy service
- **Python 3.10+**: for the SDK
- **Node.js 22+**: for the dashboard

The [development-setup guide](development-setup.md) walks through cloning, building, and running everything locally.

## Cargo workspace structure

UndoLog's Rust code lives in a Cargo workspace at the repository root:

| Crate | Path | Purpose |
|---|---|---|
| `undolog-engine` | `crates/undolog-engine/` | Tonic gRPC server, effect orchestration |
| `undolog-store` | `crates/undolog-store/` | PostgreSQL storage, migrations, queries |
| `undolog-types` | `crates/undolog-types/` | Shared types, protobuf-generated types |
| `undolog-saga` | `crates/undolog-saga/` | Saga orchestrator, compensation logic |

Build the entire workspace with `cargo build --workspace`. Run all Rust tests with `cargo test --workspace`.

## Testing expectations

Every PR that adds or modifies behaviour **must include tests**. This is non-negotiable.

| Language | Command | What to write |
|---|---|---|
| Rust | `cargo test --workspace` | Unit tests, doc tests, integration tests, property tests with `proptest` for invariants |
| Go | `go test ./... -v -count=1` | Table-driven tests, `httptest` for HTTP handlers |
| Python | `pytest -v` | Async tests with `pytest-asyncio` |

Property-based tests are preferred for validating state machine invariants, for example, "every valid state transition produces a valid next state". See the [testing guide](testing-guide.md) for details.

## Documentation expectations

- **CHANGELOG:** Add an entry under `[Unreleased]` for every user-facing change. Follow the existing format.
- **Doc comments:** Every `pub` item in Rust, every exported function in Go, and every public class or method in Python must have a doc comment. The CI enforces this for Rust with `RUSTDOCFLAGS="-D missing_docs"`.
- **ADRs:** If your change is architectural: a new component, a protocol change, a new algorithm, write an Architecture Decision Record in `docs/adr/`. See existing ADRs for the template.
- **Cross-reference:** When you add a guide, reference the relevant API pages. When you add an explanation page, reference the relevant ADRs.

For full documentation standards, see the [documentation guide](documentation-guide.md).

## Code review process

Reviewers check for:

- **Correctness:** Does the code do what the description says? Are edge cases handled?
- **Safety:** Does any new tool call path bypass the effect log? Is compensation registered before the action executes? Are advisory lock keys derived correctly?
- **Tests:** Does new behaviour have tests? Are invariants tested with property tests?
- **Documentation:** Are pub items documented? Is the CHANGELOG updated?
- **Style:** Is the code consistent with the surrounding file? (Formatters handle the rest.)

Reviewers label feedback as:
- `[block]`: must fix before merge
- `[suggest]`: worth considering but not required
- `[nit]`: style preference, author's call
- `[question]`: asking for understanding

See [COMMIT_AND_PR_STANDARDS.md](COMMIT_AND_PR_STANDARDS.md) for the full review checklist.

## AI agent contributions

AI-generated PRs follow the same standards. Additional rules:
- Every AI-generated PR must be reviewed by a human before merge.
- Include `Co-authored-by: <agent-name> <agent-identifier>` in every commit primarily written by an AI agent.
- No placeholder comments (`TODO`, `FIXME`). If something is not implemented, do not commit it.
- The human who triggers the AI agent is responsible for the code.

See [Part 7 of COMMIT_AND_PR_STANDARDS.md](COMMIT_AND_PR_STANDARDS.md#part-7--ai-agent-contribution-rules) for the full rules.

## Releasing the TypeScript SDK

The `@undobase/undolog-sdk` package is published to npm from CI with Sigstore provenance.

### Prerequisites

1. **GitHub OIDC** must be enabled in the repository settings. This allows the `npm publish --provenance` step to obtain a short-lived signing certificate without a static token. See `.github/workflows/release-sdk.yml` for the exact UI paths and configuration checklist.
2. An `NPM_TOKEN` (an npm automation token with publish scope for the `@undobase` org) must be set in the repository secrets.

### Manual release

1. Update the version in `sdks/undolog-ts/package.json` (or use `npm version`).
2. Commit the version bump with a conventional commit message (e.g. `feat(sdk): release v0.2.0`).
3. Tag the commit with the package name and version:
   ```
   git tag @undobase/undolog-sdk@v0.2.0
   git push origin @undobase/undolog-sdk@v0.2.0
   ```
4. The `.github/workflows/release-sdk.yml` workflow builds, tests, and publishes automatically.

### Dry run

A dry-run publish can be triggered locally:

```bash
cd sdks/undolog-ts
npm publish --dry-run
```

This shows the tarball contents and checks that the `files` field covers all expected assets.

## Questions

Open a discussion on the repository or reach out to the maintainers. For code-specific questions, comment on the relevant issue or PR.
