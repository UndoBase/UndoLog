## What this PR does

<!-- One or two sentences. What changes and why. This is the "why", not the "what"
(the diff shows the what). -->

## Type of change

- [ ] `feat`: new feature
- [ ] `fix`: bug fix
- [ ] `docs`: documentation only
- [ ] `refactor`: no behaviour change
- [ ] `perf`: performance improvement
- [ ] `test`: tests only
- [ ] `build` / `ci` / `chore`
- [ ] `security`: security fix
- [ ] Contains a **breaking change** (explain in footer + migration section below)

## How to test

<!-- The exact commands to run to verify this PR works. -->
<!-- If there are no tests, explain why and what manual verification was done. -->

```bash
# example
cargo test -p undolog-engine
```

## Checklist

### Code
- [ ] All new `pub` items have doc comments
- [ ] All new functions with >1 parameter document each parameter
- [ ] No `unwrap()` or `expect()` in production code paths (test code is fine)
- [ ] No `println!` or `dbg!` left in code (use `tracing::debug!` instead)
- [ ] Error variants are handled, not silenced with `let _ = `

### Tests
- [ ] New behaviour has tests
- [ ] Property tests added for any new invariant (use `proptest`)
- [ ] No tests are marked `#[ignore]` without a tracking issue in the comment

### Documentation
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] If this adds a public API: reference page updated or created
- [ ] If this changes behaviour: relevant guide updated
- [ ] If this is an architectural decision: ADR written

### Cross-language (if applicable)
- [ ] If a cross-language algorithm changed: `fixtures/signatures.json` updated
- [ ] All language implementations verified to match updated fixtures

## Breaking changes

<!-- If this PR contains a breaking change, describe exactly what breaks and
the migration path. Delete this section if no breaking change. -->

**What breaks:**

**Migration:**

## Related

<!-- Issues: Closes #XX, Fixes #XX -->
<!-- Related PRs in a stack: Follows #XX, Precedes #XX -->
<!-- ADR: docs/adr/XXXX-title.md -->
