---
title: "Publish readiness checklist"
description: "Final verification gate covering documentation links, clean-machine installs, command fidelity, registry rendering, trust sweep, and fresh-eyes review."
section: "contributing"
---
# Publish readiness checklist

This document defines the final verification gate executed before releasing any version of UndoLog. While the [Maintainer Release Process](release-process.md) details the mechanical steps for bumping versions and triggering publishing workflows, this checklist validates that all deliverables, install paths, documentation guides, and registry pages are verified against the release candidate.

A release may proceed only when all six gates pass and are recorded.

---

## Gate 1: Documentation and link integrity

Verify that documentation is structurally sound, routes render cleanly, and no dead links exist:

1. **Relative link resolution:**
   - Verify every relative markdown link across `docs/`, root `README.md`, `sdks/*/README.md`, and `examples/**/README.md`.
   - Ensure target files exist and line anchors resolve.
2. **Sidebar completeness:**
   - Inspect every section sidebar file (`docs/*/_sidebar.json`).
   - Confirm every markdown file in each section is registered in its corresponding `_sidebar.json`.
   - Confirm no unregistered orphan pages exist in the documentation tree.
3. **Index table completeness:**
   - Confirm every section index table (`docs/README.md`, `docs/guides/README.md`, `docs/reference/README.md`, `docs/contributing/README.md`, and `examples/README.md`) lists every document in its directory.
4. **Static site build verification:**
   - Build the Next.js documentation application:
     ```bash
     make test-www
     ```
   - All static routes must compile and render with zero errors.

---

## Gate 2: Clean-machine install paths

Verify each distribution path from a clean, isolated environment to ensure end-to-end functionality without local artifacts:

### 1. Python SDK clean installation

Run the following in an isolated virtual environment:

```bash
python3 -m venv /tmp/undolog-py-gate
source /tmp/undolog-py-gate/bin/activate

# Install the package
pip install undolog-sdk

# Verify import and version
python -c "import undolog_sdk; print('Installed version:', undolog_sdk.__version__)"

# Verify public exports
python -c "from undolog_sdk import run_with_session, test_compensation, test_saga; print('Exports resolved')"

# Verify CLI availability
undolog --help

deactivate
rm -rf /tmp/undolog-py-gate
```

### 2. TypeScript SDK clean installation

Run the following in an isolated directory:

```bash
mkdir -p /tmp/undolog-ts-gate && cd /tmp/undolog-ts-gate
npm init -y

# Install the package
npm install @undolog/sdk

# Verify module exports
node --input-type=module -e "import * as u from '@undolog/sdk'; console.log('Export count:', Object.keys(u).length)"

cd - && rm -rf /tmp/undolog-ts-gate
```

### 3. Build-from-clone stack verification

Verify that the local repository clone builds and runs all services:

```bash
# Build engine, proxy, and dashboard
docker compose build --parallel

# Launch stack
docker compose up -d

# Verify proxy readiness
curl -fsS http://localhost:8080/readyz

# Verify engine health
curl -fsS http://localhost:9090/healthz

# Tear down stack
docker compose down -v
```

---

## Gate 3: Command fidelity verification

Verify that every command printed in the documentation executes accurately on the release candidate:

1. **Quickstart fidelity:**
   - Execute every command in [Quickstart](../getting-started/quickstart.md) sequentially.
   - Confirm `undolog dev` starts the local development server and accepts tool calls.
2. **Installation fidelity:**
   - Execute the compose deployment in [Installation](../getting-started/installation.md).
   - Confirm ports match the shipped stack (`8080` for proxy, `9090` and `50051` for engine, `9091` for mock tool server).
3. **No invented parameters:**
   - Confirm no commands include deprecated flags, unexported environment variables, or fabricated output.

---

## Gate 4: Registry package and metadata rendering

Verify that package metadata and documentation render cleanly in destination registries:

### 1. Python SDK distribution validation

In `sdks/undolog-py/`:

1. Build distributions:
   ```bash
   python -m build
   ```
2. Validate distribution metadata:
   ```bash
   twine check dist/*
   ```
3. Inspect package archive contents:
   - Confirm `LICENSE` (Apache-2.0) is present in both `.tar.gz` (sdist) and `.whl` (wheel).
   - Confirm `README.md` renders cleanly as the package description without unresolved relative links to repository directories.

### 2. TypeScript SDK distribution validation

In `sdks/undolog-ts/`:

1. Pack dry run:
   ```bash
   npm pack --dry-run
   ```
2. Verify package payload:
   - Confirm `LICENSE` is included in the package allowlist alongside `dist/`.
   - Confirm `package.json` specifies `"type": "module"`, exports maps, and repository URLs.

---

## Gate 5: Trust and security sweep

Verify security disclosures, licensing integrity, and contact channels:

1. **Security policy presence:**
   - Verify [SECURITY.md](../../SECURITY.md) exists in the repository root.
   - Verify the root `README.md` links to `SECURITY.md` in its Security section.
2. **License compliance:**
   - Verify root `LICENSE` contains the standard Apache-2.0 license text.
   - Verify matching copies exist in `sdks/undolog-py/LICENSE` and `sdks/undolog-ts/LICENSE`.
3. **Vulnerability disclosure contact:**
   - Confirm the contact address (`security@undobase.com`) is active and monitored according to policy response timelines.
4. **Dependency hygiene:**
   - Run linter and vulnerability checks across components:
     ```bash
     make check
     ```

---

## Gate 6: Fresh-eyes walkthrough

Before declaring release readiness, an engineer or contributor must run a fresh-eyes walkthrough:

1. **New machine or clean container:**
   - Use an environment with no pre-existing caches, local environment variables, or uncommitted files.
2. **First-run path:**
   - Follow [Quickstart](../getting-started/quickstart.md) using only the commands printed on the page.
   - Do not rely on unstated assumptions or developer tools.
3. **Defect recording:**
   - Any point of ambiguity, confusing error message, or missing prerequisite must be documented as an issue and resolved before tagging.

---

## Release sign-off checklist

Record the results of the six gates prior to tagging:

| Gate | Status | Verified by | Date | Notes |
|---|---|---|---|---|
| Gate 1: Documentation and links | [ ] Pass | | | Next.js build clean |
| Gate 2: Clean installs | [ ] Pass | | | PyPI, npm, and clone |
| Gate 3: Command fidelity | [ ] Pass | | | Getting-started commands run |
| Gate 4: Registry rendering | [ ] Pass | | | PyPI and npm dry runs |
| Gate 5: Trust and security | [ ] Pass | | | SECURITY.md and LICENSE checked |
| Gate 6: Fresh-eyes walkthrough | [ ] Pass | | | Quickstart verified |
