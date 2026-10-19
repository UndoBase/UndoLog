---
title: "Maintainer release process"
description: "Step-by-step release process, version bump map, tag triggers, verification, and rollback procedures."
section: "contributing"
---
# Maintainer release process

This document defines the release process for UndoLog maintainers. It serves as an actionable checklist for coordinating releases across the repository packages, cutting the changelog, triggering automated publishing workflows, verifying artifacts from clean environments, and mitigating release defects.

---

## Release overview

UndoLog is maintained as a monorepo containing multiple independently versioned deliverables:

| Component | Repository path | Published artifact | Distribution channel |
|---|---|---|---|
| Python SDK | `sdks/undolog-py/` | `undolog-sdk` | PyPI |
| TypeScript SDK | `sdks/undolog-ts/` | `@undolog/sdk` | npm registry |
| Helm chart | `deploy/helm/undolog/` | `undolog` | Repository and chart directory |
| Rust engine and tools | `crates/` | `undolog-engine`, `undolog-migrate` | Source and local container build |
| Go MCP proxy | `services/undolog-proxy/` | `undolog-proxy` | Source and local container build |
| Web applications | `apps/dashboard/`, `apps/www/` | Dashboard and documentation site | Static site build and container |

All packages follow [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`). Releases are prepared on a dedicated release branch, verified locally, reviewed via pull request, and published by pushing git tags.

---

## Pre-flight checks and registry readiness

Before beginning the release cut, maintainers must verify registry permissions and upstream prerequisites:

### 1. npm scope ownership

- Confirm administrative access to the `@undolog` scope on npmjs.com.
- Ensure the publishing bot or release token is configured with write permissions for `@undolog/sdk`.
- Workflow `.github/workflows/release-sdk.yml` publishes packages with provenance attestations requiring GitHub OpenID Connect (OIDC) permissions (`id-token: write`).

### 2. PyPI project and trusted publishing

- Confirm that the `undolog-sdk` project exists on PyPI (pypi.org).
- Ensure Trusted Publishing is active for the `UndoBase/UndoLog` repository:
  - Owner: `UndoBase`
  - Repository: `UndoLog`
  - Workflow name: `release-python-sdk.yml`
  - Environment: `pypi`
- Trusted Publishing eliminates static API tokens, relying on GitHub short-lived OIDC tokens.

### 3. Container image registry status

- No public container registry publishes pre-built UndoLog images today.
- Deployment guides instruct operators to build images locally from the repository clone using `infra/Dockerfile.engine` and `infra/Dockerfile.proxy`.
- When an official container registry is wired into continuous deployment, the image build and push steps will be incorporated into this release checklist.

---

## Version bump map

When cutting a release, every file carrying a version must be updated in a single coordinated commit on a release branch (`release/vX.Y.Z`).

| Order | Target file | Version field | Description |
|---|---|---|---|
| 1 | `sdks/undolog-py/pyproject.toml` | `project.version` | Python SDK package version |
| 2 | `sdks/undolog-ts/package.json` | `version` | TypeScript SDK package version |
| 3 | `Cargo.toml` | `workspace.package.version` | Rust workspace version, inherited by all workspace crates |
| 4 | `deploy/helm/undolog/Chart.yaml` | `version` | Helm chart release version |
| 5 | `deploy/helm/undolog/Chart.yaml` | `appVersion` | Deployed application version string |
| 6 | `apps/dashboard/package.json` | `version` | Dashboard web application version |
| 7 | `apps/www/package.json` | `version` | Documentation site application version |
| 8 | `CHANGELOG.md` | `## [Unreleased]` | Cut unreleased entries under `## [X.Y.Z] - YYYY-MM-DD` |
| 9 | `docs/changelog/` | New release notes file | Add `release-notes-vX.Y.Z.md` per release notes template |

### Bump procedure

1. Create a release branch from `main`:
   ```bash
   git checkout main && git pull
   git checkout -b release/vX.Y.Z
   ```
2. Update the version strings in each file listed in the table above.
3. Validate that workspace configurations parse cleanly:
   ```bash
   make check
   ```
4. Commit the changes and open a pull request:
   ```bash
   git add -u
   git commit -m "chore(release): prepare vX.Y.Z"
   git push -u origin release/vX.Y.Z
   ```
5. Merge the pull request to `main` once all continuous integration checks pass.

---

## Tag conventions and automated workflows

Releases are published through automated GitHub Actions triggered by specific git tag conventions.

### Summary of tag triggers

| Package | Tag convention | GitHub workflow file | Triggered action |
|---|---|---|---|
| Python SDK | `undolog-sdk@v*.*.*` | `.github/workflows/release-python-sdk.yml` | Builds sdist and wheel, verifies with twine, publishes to PyPI |
| TypeScript SDK | `@undolog/sdk@v*.*.*` | `.github/workflows/release-sdk.yml` | Runs unit tests, builds package, publishes to npm with provenance |
| Monorepo release | `v*.*.*` | Manual GitHub release | Creates repository release and archives documentation notes |

### 1. Releasing the Python SDK

1. Switch to `main` with the merged release commit:
   ```bash
   git checkout main && git pull
   ```
2. Create and push the Python SDK tag:
   ```bash
   git tag -a "undolog-sdk@vX.Y.Z" -m "undolog-sdk vX.Y.Z"
   git push origin "undolog-sdk@vX.Y.Z"
   ```
3. Monitor `.github/workflows/release-python-sdk.yml`:
   - Runs `ruff check .` and `ruff format --check .`.
   - Runs `mypy undolog_sdk/ tests/` and `pytest tests/ -v`.
   - Executes `python -m build` to generate `dist/` sdist and wheel.
   - Runs `twine check dist/*` to validate metadata.
   - Publishes to PyPI via `pypa/gh-action-pypi-publish@release/v1`.

### 2. Releasing the TypeScript SDK

1. Create and push the TypeScript SDK tag:
   ```bash
   git tag -a "@undolog/sdk@vX.Y.Z" -m "@undolog/sdk vX.Y.Z"
   git push origin "@undolog/sdk@vX.Y.Z"
   ```
2. Monitor `.github/workflows/release-sdk.yml`:
   - Installs dependencies using `npm ci`.
   - Runs unit tests via `npm run test:unit`.
   - Publishes to npm using `npm publish --provenance`.

### 3. Releasing the monorepo tag

1. Create and push the overall repository tag:
   ```bash
   git tag -a "vX.Y.Z" -m "UndoLog vX.Y.Z"
   git push origin "vX.Y.Z"
   ```
2. Open GitHub Releases and create a release for `vX.Y.Z`:
   - Title: `UndoLog vX.Y.Z`
   - Description: Copy contents from `docs/changelog/release-notes-vX.Y.Z.md`.

---

## Changelog cut procedure

Before tagging, convert unreleased changes into the official version section in `CHANGELOG.md`:

1. Inspect `CHANGELOG.md` under `## [Unreleased]`.
2. Insert the version header and date directly below `## [Unreleased]`:
   ```markdown
   ## [Unreleased]

   ## [X.Y.Z] - YYYY-MM-DD
   ```
3. Move all documented unreleased changes under the new `## [X.Y.Z]` heading, retaining standard Keep a Changelog category ordering:
   - `### Added`
   - `### Changed`
   - `### Deprecated`
   - `### Removed`
   - `### Fixed`
   - `### Security`
4. Update the comparison links at the bottom of `CHANGELOG.md`:
   ```markdown
   [Unreleased]: https://github.com/UndoBase/UndoLog/compare/vX.Y.Z...HEAD
   [X.Y.Z]: https://github.com/UndoBase/UndoLog/compare/v<PREVIOUS_VERSION>...vX.Y.Z
   ```
5. Author a release notes document in `docs/changelog/release-notes-vX.Y.Z.md` following `docs/contributing/release-notes-template.md` and link it in `docs/changelog/_sidebar.json`.

---

## Post-release verification

Verify every published package from clean environments before declaring the release complete:

### 1. Python SDK verification (isolated venv)

Run the following in a temporary shell:

```bash
python3 -m venv /tmp/undolog-py-verify
source /tmp/undolog-py-verify/bin/activate

# Install the published package from PyPI
pip install undolog-sdk==X.Y.Z

# Verify package metadata and version
python -c "import undolog_sdk; print('Version:', undolog_sdk.__version__)"

# Verify core exports
python -c "from undolog_sdk import run_with_session, test_compensation, test_saga; print('Exports verified')"

# Verify CLI entrypoint
undolog --help

deactivate
rm -rf /tmp/undolog-py-verify
```

### 2. TypeScript SDK verification (isolated directory)

Run the following in a temporary directory:

```bash
mkdir -p /tmp/undolog-ts-verify && cd /tmp/undolog-ts-verify
npm init -y

# Install the published package from npm
npm install @undolog/sdk@X.Y.Z

# Inspect published metadata
npm view @undolog/sdk@X.Y.Z version dist-tags license

# Verify ESM import and exports
node --input-type=module -e "import * as u from '@undolog/sdk'; console.log('Export count:', Object.keys(u).length)"

cd - && rm -rf /tmp/undolog-ts-verify
```

### 3. Docker stack verification from repository clone

Verify that the repository clone builds and runs the stack cleanly:

```bash
# Build engine, proxy, and dashboard
docker compose build --parallel

# Start containers in background
docker compose up -d

# Verify proxy readiness
curl -fsS http://localhost:8080/readyz

# Verify engine health
curl -fsS http://localhost:9090/healthz

# Clean up containers
docker compose down -v
```

---

## Rollback and incident mitigation

If a defect or security regression is discovered in a published release, follow these steps:

### 1. PyPI package remediation

PyPI strictly disallows re-uploading an existing version string. If a release is broken:

1. **Yank the defective release:**
   ```bash
   pip install twine
   twine yank undolog-sdk --version X.Y.Z --reason "Critical defect in session state handling"
   ```
   Yanking ensures package installers (such as `pip` and `poetry`) skip the version unless explicitly pinned, protecting new installs without breaking existing pinned builds.
2. **Publish a patch release:**
   Commit the fix, bump the version to `X.Y.(Z+1)`, and tag `undolog-sdk@vX.Y.(Z+1)`.

### 2. npm package remediation

npm prohibits unpublishing packages older than 72 hours and discourages unpublishing if dependents exist. If a release is broken:

1. **Deprecate the defective version:**
   ```bash
   npm deprecate @undolog/sdk@X.Y.Z "Critical defect in saga execution: upgrade to X.Y.W immediately"
   ```
   Deprecation surfaces a prominent warning during user installation without breaking automated build pipelines.
2. **Publish a patch release:**
   Commit the fix, bump the version to `X.Y.(Z+1)`, and tag `@undolog/sdk@vX.Y.(Z+1)`.

### 3. Git tags and security advisories

- Do not delete or force-push git tags that have been pushed to origin. Immutability guarantees auditability.
- Update the GitHub release description with a warning directing users to the replacement version.
- If the issue involves a security vulnerability, follow the coordinated disclosure and patch procedures documented in [SECURITY.md](../../SECURITY.md).
