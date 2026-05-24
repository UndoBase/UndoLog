---
title: "Documentation guide"
description: "UndoLog documentation follows the [Diátaxis framework](https://diataxis.fr/), which divides documentation into four layers: tutorials, how-to guides, reference, and explanation."
section: "contributing"
---
# Documentation guide

UndoLog documentation follows the [Diátaxis framework](https://diataxis.fr/), which divides documentation into four layers: tutorials, how-to guides, reference, and explanation.

## Four layers

| Layer | Directory | Purpose | Audience |
|---|---|---|---|
| **Tutorials** | `docs/getting-started/` | Step-by-step lessons that get a new user started | New users |
| **How-to guides** | `docs/guides/` | Practical steps for specific tasks | Users who know what they want to do |
| **Reference** | `docs/reference/` | Technical descriptions of APIs, config, and schemas | Users who need exact details |
| **Explanation** | `docs/explanation/` | Background, rationale, and design discussion | Users who want to understand why |

Do not mix layers in a single document. A how-to guide does not contain API reference tables. A reference page does not include step-by-step tutorials. Cross-reference between layers instead.

## Architecture Decision Records

Every significant architectural decision gets an ADR in `docs/adr/`. ADRs record the context, options considered, and the final decision.

An ADR is required when:

- Adding a new component or service
- Changing a protocol or wire format
- Introducing a new algorithm (especially cross-language)
- Changing the state machine or safety model
- Adopting or changing a major dependency

See existing ADRs in `docs/adr/` for the template.

## Changelog

User-facing changes must have an entry in `CHANGELOG.md` under `[Unreleased]`. Group entries by type (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`). Use imperative mood and link to the relevant PR.

## Doc comments

**Rust:**

Every `pub` item must have a doc comment (`///`). The CI enforces this with `RUSTDOCFLAGS="-D missing_docs"`. Use doc tests for examples:

```rust
/// Registers a compensation handler for the given effect.
///
/// The handler is called if the saga needs to undo this effect.
/// Returns the compensation ID on success.
///
/// # Errors
///
/// Returns `StoreError::DuplicateSignature` if the effect has already been registered.
pub async fn register_compensation(&self, effect_id: EffectId) -> Result<CompensationId, StoreError>
```

**Go:**

Exported functions, types, and constants must have a doc comment. Use the convention `// FunctionName does X`:

```go
// RegisterEffect registers a new effect in the journal. It returns the
// effect ID or an error if the effect signature already exists.
func (s *Store) RegisterEffect(ctx context.Context, effect *Effect) (*EffectID, error)
```

**Python:**

Public classes, methods, and functions must have a docstring. Use Google-style docstrings:

```python
def register_compensation(effect_id: str, handler_url: str) -> str:
    """Register a compensation handler for an effect.

    Args:
        effect_id: The effect to register compensation for.
        handler_url: The URL that handles compensation.

    Returns:
        The compensation ID string.

    Raises:
        DuplicateSignatureError: If the effect already has a compensation.
    """
```

## Cross-referencing

When you add a guide, reference the relevant reference pages:

```markdown
See the [Call Signature spec](../reference/call-signature.md) for the exact
algorithm.
```

When you add an explanation page, reference the relevant ADRs:

```markdown
See [ADR 0001](../adr/0001-rust-for-effect-engine.md) for why the engine
is written in Rust.
```

Links must use relative paths and end in `.md`. The build process converts them to final URLs.

## Checklist for documentation PRs

Before marking a documentation PR as ready for review:

- [ ] A section has been added to the correct Diátaxis layer
- [ ] Links to related pages are included and correctly formatted
- [ ] The page follows the existing template for that section
- [ ] Code examples are tested and produce the output shown
- [ ] No marketing language, no future promises, no speculative claims
- [ ] The CHANGELOG has an entry under `[Unreleased]`
- [ ] Cross-references from other pages are updated if the page URL changed

## Building the docs site (future)

The docs site will be built with mkdocs. A `mkdocs.yml` configuration will be added at the repository root. For now, all documentation is plain Markdown files that render correctly on GitHub.
