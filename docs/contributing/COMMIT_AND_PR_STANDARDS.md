---
title: "Contribution & Commit Standards"
description: "## The organization-wide standard for all open source projects"
section: "contributing"
---
# Contribution & Commit Standards
## The organization-wide standard for all open source projects

**Version:** 1.0.0
**Applies to:** Every repository in this organization.
**Status:** Binding. CI enforces compliance. PRs that violate these standards are not merged.

---

## Philosophy in one sentence

**Every commit tells a story. Every PR is a conversation. Every review is a responsibility.**

A clean Git history is not aesthetic preference: it is a diagnostic tool. When something breaks in production at 2 AM, the commit history is what tells you why. When a new contributor tries to understand a decision made six months ago, the commit body is what gives them context. When an automated tool generates a changelog or bumps a version, it reads commit types to decide what changed. This standard exists because Git hygiene compounds over time: a well-maintained history saves hours of debugging and days of onboarding.

---

## Part 1. Commits

### 1.1 Format

Every commit message follows the Conventional Commits specification exactly.

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

**Line length rules:**
- Subject line (type + scope + description): maximum **72 characters**
- Body lines: maximum **72 characters**, wrap at word boundaries
- Footer lines: no limit

---

### 1.2 Types

These are the only valid types. No others. Type is always lowercase.

| Type | When to use | SemVer impact |
|---|---|---|
| `feat` | A new capability that users or integrators can observe | MINOR |
| `fix` | Corrects a defect in existing behaviour | PATCH |
| `docs` | Documentation only: no code change | none |
| `refactor` | Code change that neither adds a feature nor fixes a bug. Behaviour is identical before and after. If behaviour changes at all, it is not a refactor. | none |
| `perf` | Code change that improves performance without changing behaviour | PATCH |
| `test` | Adding or correcting tests only | none |
| `build` | Changes to build system, Cargo.toml, go.mod, package.json, Dockerfiles, Makefiles | none |
| `ci` | Changes to CI/CD workflows and scripts | none |
| `chore` | Maintenance that does not fit any above type: dependency bumps, lockfile updates, tooling config | none |
| `security` | A fix that addresses a security vulnerability. Always treated as PATCH minimum. | PATCH |
| `revert` | Reverts a previous commit. Must reference the reverted commit hash in the footer. | varies |

**When in doubt between two types, use the one that describes purpose, not object.**
Refactoring a test file → `refactor`, not `test`.
Fixing a performance bug → `fix`, not `perf`.

---

### 1.3 Scopes

Scopes are optional but strongly recommended. They identify the part of the codebase affected. Use the names that match the actual folders, crates, or services.

| Scope | What it covers |
|---|---|
| `engine` | crates/undolog-engine |
| `store` | crates/undolog-store |
| `types` | crates/undolog-types |
| `saga` | crates/undolog-saga |
| `proxy` | services/undolog-proxy |
| `sdk-py` | sdks/undolog-py |
| `sdk-ts` | sdks/undolog-ts |
| `dashboard` | apps/dashboard |
| `schema` | database migrations |
| `proto` | proto/undolog.proto |
| `infra` | Docker, CI, deployment |
| `docs` | docs/ folder |
| `deps` | dependency updates across any crate/service |

When a commit touches multiple scopes, use the most significant one. If genuinely equal, omit the scope.

---

### 1.4 Description

The description is the imperative-mood summary of what the commit does.

**Rules:**
- Imperative mood: "add", "fix", "remove", "update": not "added", "fixes", "removing"
- Lowercase first letter
- No period at the end
- Describes what the commit does, not what you did: "add BLAKE3 call signature" not "added the new BLAKE3 hashing for signatures"
- Maximum 72 characters including type and scope

**Good:**
```
feat(engine): add BLAKE3 call signature for exactly-once semantics
fix(store): correct advisory lock key on empty signature string
docs(saga): document LIFO compensation order invariant
refactor(types): extract canonical_json into dedicated module
```

**Bad:**
```
feat: added some stuff to the engine         ← vague, past tense
fix: fixed the bug                           ← no scope, no information
update code                                  ← no type, no scope
WIP                                          ← never commit WIP to main
feat(engine): Add BLAKE3 Call Signature.     ← capital letters, period
```

---

### 1.5 Body

The body is optional but required in these cases:
- The commit introduces a non-obvious decision
- The commit fixes a bug whose cause is not apparent from the code
- The commit changes behaviour that another developer might reverse by accident

**Body rules:**
- Blank line between subject and body (mandatory)
- Wrap at 72 characters
- Uses present tense for describing the current state: "The advisory lock uses FNV-1a"
- Uses past tense only when describing what changed: "Previously, the lock used CRC32"
- Answers **why**, not **what**: the diff already shows what

**Example:**

```
fix(store): prevent double-compensation on crash-recovery restart

When the Saga Orchestrator restarts after a process crash, it reloads
the undo stack and begins walking it. Previously, entries in 'running'
state would be retried even if the compensation HTTP call had already
completed: causing the compensation endpoint to receive two calls.

The fix transitions entries to 'running' only when the HTTP call is
about to be made, and checks for 'compensated' state before executing.
Idempotency keys on the compensation endpoint handle the edge case
where the state transition succeeds but the process crashes before
marking the entry as compensated.
```

---

### 1.6 Footers

Footers are key-value pairs after a blank line following the body (or subject, if no body).

**Standard footers:**

```
Closes #123
Fixes #456
BREAKING CHANGE: <description of what breaks and how to migrate>
Co-authored-by: Name <email>
Reviewed-by: Name <email>
Refs: #789
```

**Breaking changes:**

A commit with a breaking change MUST include `BREAKING CHANGE:` in the footer with a full migration description. It MAY also use `!` after the type for visibility.

```
feat(engine)!: replace InterceptOutcome::Execute variant with ExecuteEffect

BREAKING CHANGE: The Execute variant of InterceptOutcome has been renamed
to ExecuteEffect and now carries a `tier` field alongside `effect_id`.

Before:
  InterceptOutcome::Execute { effect_id }

After:
  InterceptOutcome::ExecuteEffect { effect_id, tier }

Update all match arms that destructure Execute to use ExecuteEffect.
```

---

### 1.7 What belongs in one commit

One commit = one logical change. Not one file. Not one function. One logical change.

**A logical change is a unit that:**
- Can be described in a single subject line
- Can be reverted without reverting unrelated work
- Leaves the codebase in a working state (tests pass, code compiles)

**Split into multiple commits when:**
- A refactor is mixed with a bug fix (always separate these)
- A feature has independent parts that could be useful alone
- A dependency update is mixed with code changes that use it

**Do NOT split when:**
- A test and the code it tests are written together: they are one logical change
- A function and its doc comment are written together: they are one logical change
- A migration and the code that uses it must deploy together, they are one logical change

**Atomic rule:** Every commit in the history must compile and pass all tests. Never commit broken code to any branch, including feature branches. If you need to save work in progress, use `git stash`.

---

### 1.8 Commit size guideline

| Size | Lines changed | Guideline |
|---|---|---|
| Ideal | < 200 | Easy to review, easy to revert |
| Acceptable | 200–500 | Fine if it is genuinely one change |
| Large | 500–1000 | Requires justification in the body |
| Problematic | > 1000 | Must be broken up unless it is a generated file or migration |

Generated files (Cargo.lock, go.sum, protobuf output) are exempt from size limits.

---

## Part 2. Branches

### 2.1 Branching strategy

This organization uses **GitHub Flow**: one permanent branch (`main`) plus short-lived feature branches. GitFlow (develop, release, hotfix branches) is explicitly rejected: it creates long-lived branches that diverge from main and make CI slower and merges more painful.

**Rules:**
- `main` is always deployable. Every commit on main passes CI.
- Feature branches are short-lived: open for days, not weeks.
- No direct commits to `main` by any contributor including maintainers.
- `main` is protected: requires PR + CI pass + 1 approval minimum.

---

### 2.2 Branch naming

```
<type>/<short-description>
```

The `<type>` matches the commit type the branch will produce. The `<short-description>` uses hyphens, lowercase, no special characters.

| Branch name | Meaning |
|---|---|
| `feat/saga-orchestrator` | New feature: the saga orchestrator |
| `fix/advisory-lock-empty-string` | Bug fix: advisory lock on empty string |
| `docs/adr-blake3-decision` | Documentation: ADR for BLAKE3 |
| `refactor/effect-store-row-mapper` | Refactor: the row mapper in effect store |
| `test/engine-integration-suite` | New test suite for the engine |
| `ci/add-rust-clippy-job` | CI change: add Clippy job |
| `chore/bump-sqlx-0-8-2` | Dependency bump |
| `security/fix-advisory-lock-collision` | Security fix |

**Rules:**
- No `my-branch`, `temp`, `test123`, `wip`, or personal name prefixes
- Maximum 50 characters for the full branch name
- Delete the branch immediately after the PR is merged
- Never reuse a branch name

---

### 2.3 Branch lifetime limits

| Branch type | Maximum lifetime |
|---|---|
| `feat/*` | 5 working days |
| `fix/*` | 2 working days |
| `docs/*` | 3 working days |
| `refactor/*` | 3 working days |
| `chore/*` | 1 working day |

Branches that exceed their limit without a PR are closed by the maintainer with a comment explaining why. The work is not lost, it is committed and the branch is deleted; the contributor opens a new PR.

---

## Part 3. Pull Requests

### 3.1 PR size rules

| Category | Max lines changed | Exceptions |
|---|---|---|
| Code PR | 500 lines | None |
| Refactor PR | 800 lines | If the refactor is mechanical (rename, move) |
| Documentation PR | No limit |, |
| Migration PR | No limit | Schema-only, no logic |
| Generated files | No limit | Cargo.lock, go.sum, protobuf stubs |

If your change legitimately exceeds 500 lines, split it into a stack of PRs where each PR builds on the previous one. Open them in sequence. The description of each PR links to the others in the stack.

---

### 3.2 PR title format

PR titles follow the same format as commit subject lines:

```
<type>(<scope>): <description>
```

The PR title becomes the squash-merge commit message. It must be valid Conventional Commits format because the CI commit linter validates it.

---

### 3.3 PR description template

Located at `.github/PULL_REQUEST_TEMPLATE.md`: pre-filled for every PR.

---

### 3.4 PR rules

**Author responsibilities (before opening the PR):**

1. **Self-review first.** Read your own diff line by line before requesting review. Catch your own typos, debug logs, and obvious mistakes. A PR that clearly hasn't been self-reviewed will be returned without review.

2. **CI must pass.** Never open a PR with failing CI. Fix it first. The only exception is a draft PR opened to discuss an approach before the implementation is complete.

3. **Rebase, do not merge.** Keep your branch rebased on `main`. Never merge `main` into your feature branch, rebase instead. A branch that is merge-polluted (contains merge commits from `main`) will be asked to rebase before review.

4. **One concern per PR.** If you discover a bug while implementing a feature, fix the bug in a separate PR first, then build the feature on top of that fix. Do not bundle unrelated changes.

5. **Draft PRs are welcome.** If you want early feedback on direction, open a draft PR with `[WIP]` in the title. Draft PRs are not reviewed for code quality, only for architectural direction.

**Reviewer responsibilities:**

1. **Respond within 2 working days.** If you cannot review within 2 days, say so and suggest another reviewer.

2. **Distinguish blocking from non-blocking feedback.** Use explicit labels:
   - `[block]`: must be fixed before merge
   - `[suggest]`: worth considering but not required
   - `[nit]`: style preference, author's call
   - `[question]`: asking for understanding, not requesting a change

3. **Review the whole PR, not just the diff.** Check that tests exist, docs are updated, CHANGELOG is updated, and the commit message is correct.

4. **Approve means you are confident it is correct.** Not "looks fine to me", confident. If you are not confident about a section, say so explicitly rather than approving anyway.

5. **Be specific.** "This could be better" is not useful feedback. "This function will panic if `args` is an empty JSON object because `canonical_json` does not handle empty objects, see line 47" is useful feedback.

---

### 3.5 Merge strategy

**Squash merge only.**

Every PR is merged as a single squash commit onto `main`. The squash commit message is the PR title (which must be valid Conventional Commits format). The full PR description body becomes the squash commit body.

This is non-negotiable. Merge commits and rebase-merge are disabled on every repository in this organization.

**Why squash:**
- `main` history reads as one commit per logical change: clean and greppable
- Individual commits on a feature branch can be messy during development, that is fine, they disappear on merge
- `git bisect` works correctly because every commit on main is a deployable state
- `git log --oneline` on main is a readable changelog

---

### 3.6 Who can merge

- The PR author merges after receiving the required approvals and CI passes
- The author does not approve their own PR
- Maintainers may merge with 0 approvals only for: `docs` type PRs, `chore` type dependency bumps where CI passes, and emergency `security` fixes with a post-merge review

---

## Part 4. Code Review Standards

### 4.1 What reviewers check

**Correctness (always check):**
- Does the code do what the description says?
- Are there edge cases not handled? (empty inputs, zero values, concurrent calls, process crashes)
- Are errors handled, or silently swallowed?
- For Rust: are there `.unwrap()` calls that will panic in production?
- For async code: are there potential deadlocks or race conditions?

**Safety. UndoLog-specific invariants (always check):**
- Does any new tool call path bypass the effect log?
- Is compensation always registered before the action executes?
- Are advisory lock keys derived identically across languages if a new algorithm is introduced?
- Is any new state transition impossible to leave the system in an inconsistent state on crash?

**Tests:**
- Does new behaviour have tests?
- Are invariants tested with property tests (proptest) rather than just example cases?
- Do tests test behaviour, not implementation? (Tests that break on refactoring are weak tests)

**Documentation:**
- Does every new `pub` item have a doc comment?
- Is the CHANGELOG updated?
- Are new cross-language algorithms in the reference docs with fixture test vectors?

**Style:**
- Is code consistent with the surrounding file? (not just the formatter, naming, structure, comment style)
- Are error messages in the same tone as existing error messages?
- Are tracing fields in the same format as existing trace calls?

### 4.2 What reviewers do NOT check

- Whitespace and formatting: the formatter handles this. CI enforces it. Do not leave formatting comments.
- Personal style preferences not covered by the project standard, keep them to yourself or open a discussion to update the standard
- Whether you would have implemented it differently: only raise this if the approach has a correctness or maintainability problem

---

## Part 5. Releases

### 5.1 Version numbering

Strict Semantic Versioning: `MAJOR.MINOR.PATCH`

| Increment | When |
|---|---|
| `MAJOR` | A `BREAKING CHANGE` footer appears in any commit since the last release |
| `MINOR` | A `feat` commit appears since the last release (no breaking changes) |
| `PATCH` | Only `fix`, `perf`, `security` commits since the last release |
| No change | Only `docs`, `test`, `build`, `ci`, `chore`, `refactor` commits |

Version 0.x.x: During pre-1.0 development, MINOR bumps MAY include breaking changes. This will be explicitly noted in the CHANGELOG.

### 5.2 Release process

1. Update `CHANGELOG.md`: move everything under `[Unreleased]` to a new `[X.Y.Z]. YYYY-MM-DD` section
2. Bump version in all manifests (`Cargo.toml` workspace package version, `pyproject.toml`, `package.json`)
3. Open a PR titled `chore(release): prepare v0.2.0`: this is the only PR type that touches the version
4. After merge: tag the squash commit: `git tag v0.2.0` and push the tag
5. The CI release job triggers on version tags: runs tests, builds binaries, publishes crates/packages, creates GitHub release with changelog section as body

### 5.3 Hotfixes

A hotfix is a `fix` or `security` commit that must reach users before the next planned release.

Process: branch from the version tag (`git checkout -b fix/critical-bug v0.1.3`), apply the fix, open PR against `main`, merge, tag the PATCH bump (`v0.1.4`), cherry-pick back to any active release branches if needed.

---

## Part 6. Issues

### 6.1 Issue types

Every issue uses one of three templates (located at `.github/ISSUE_TEMPLATE/`):

- **Bug report** (`bug_report.md`): for reporting defects
- **Feature request** (`feature_request.md`): for suggesting new capabilities
- **Documentation issue** (`documentation_issue.md`): for reporting documentation problems

### 6.2 Issue rules

- Every bug fix PR must close an issue: `Closes #XX` in the footer
- Feature PRs should reference an issue where the feature was discussed: `Refs: #XX`
- Issues are not closed without a resolution: either fixed, explicitly declined with explanation, or marked `wont-fix`
- Never close an issue with only "fixed": link to the commit or PR

---

## Part 7. AI agent contribution rules

When an AI agent (Claude, Copilot, Cursor, or any other) opens commits or PRs on this repository, the same standards apply without exception. AI-generated commits that do not follow this standard will be rejected by CI.

**Additional rules for AI-generated contributions:**

1. **Every AI-generated PR must be reviewed by a human before merge.** No AI agent has auto-merge permissions.

2. **The PR description must include:** `Co-authored-by: <agent-name> <agent-identifier>` in the footer of every commit that was primarily written by an AI agent.

3. **AI agents must not write `// TODO`, `// FIXME`, or placeholder comments.** If something is not implemented, it is not committed. Partial implementations must be behind a feature flag or in a draft PR.

4. **AI agents must run the full test suite before opening a PR.** No PR from an AI agent is opened with failing tests.

5. **AI agents follow the documentation standard.** Every `pub` item has a doc comment. CHANGELOG is updated. If the change is architectural, an ADR is written.

6. **The human who triggers the AI agent is responsible for the code.** If an AI agent writes incorrect or insecure code and a human approves it, the human is accountable.

---

## Part 8. Enforcement

### 8.1 CI checks (automated, blocking)

All of these must pass before any PR can be merged:

| Check | Tool | What it validates |
|---|---|---|
| Commit format | `commitlint` | Every commit in the PR is valid Conventional Commits |
| PR title format | `commitlint` (via GitHub Action) | PR title matches `type(scope): description` |
| Rust formatting | `rustfmt` | All Rust code is formatted |
| Rust linting | `clippy --deny warnings` | No Clippy warnings |
| Rust docs | `RUSTDOCFLAGS="-D missing_docs" cargo doc` | All pub items documented |
| Go formatting | `gofmt -l` | All Go code is formatted |
| Go linting | `golangci-lint` | No lint warnings |
| Python formatting | `ruff format --check` | All Python code is formatted |
| Python linting | `ruff check` | No lint warnings |
| Tests. Rust | `cargo test --workspace` | All tests pass |
| Tests. Go | `go test ./...` | All tests pass |
| Tests. Python | `pytest` | All tests pass |
| Broken links | `markdown-link-check` | No broken links in docs/ |
| CHANGELOG | Custom script | `[Unreleased]` section exists and is non-empty for non-chore PRs |

### 8.2 Human review (blocking)

- Minimum 1 approval from a maintainer
- All `[block]` comments resolved
- PR author has responded to every `[question]` comment

### 8.3 What happens when standards are violated

- **Commit format violation:** CI fails. The author must amend or rebase to fix the commit messages before the PR can be reviewed.
- **Missing doc comment:** CI fails. The author adds the doc comment.
- **Missing CHANGELOG update:** CI fails. The author adds the entry.
- **PR too large:** The reviewer requests a split before starting review. The original PR is closed; smaller PRs replace it.
- **Failing tests:** PR is not reviewed until CI is green.
- **Reviewer disagreement:** If an author and reviewer disagree on a `[block]` comment, a second maintainer is asked to decide. The decision is final.

---

*This document is versioned. Changes require a PR with type `docs(contributing)` and must be approved by at least 2 maintainers.*
