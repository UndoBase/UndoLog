# Security Policy

UndoLog treats security as a core architectural property. This document outlines our vulnerability disclosure process, response timeline, and supported versions.

## Supported Versions

UndoLog provides security patches and remediation updates for the following release lines:

| Version | Supported | Notes |
|---|---|---|
| 0.1.x | Yes | Active development line |
| < 0.1.0 | No | Unsupported pre-release builds |

For compatibility between specific SDK, engine, and proxy releases, refer to the [Compatibility Matrix](docs/reference/compatibility-matrix.md).

## Reporting a Vulnerability

If you believe you have discovered a security vulnerability in UndoLog, report it to our security team via email:

- **Email:** [security@undobase.com](mailto:security@undobase.com)

Please do not open a public GitHub issue or discussion for an unresolved vulnerability.

### Report Contents

To help us triage and remediate the issue promptly, please include as much of the following information as possible:

- **Component:** The affected area (for example: engine gRPC contract, proxy HTTP/SSE endpoints, Python SDK, or TypeScript SDK).
- **Description:** A detailed explanation of the vulnerability and its potential security impact.
- **Reproduction:** Clear, step-by-step reproduction instructions or a minimal proof of concept script.
- **Environment:** Relevant versions of UndoLog, runtime (Python, Node.js, Go, Rust), and operating system.
- **Mitigation:** Any proposed patches, workarounds, or defensive configurations you have identified.

### Response Process

When you report a vulnerability to [security@undobase.com](mailto:security@undobase.com), our team follows this timeline:

1. **Acknowledgement:** We acknowledge receipt of your vulnerability report within 48 hours.
2. **Triage:** We assess the technical severity, reproduction steps, and impact within 5 business days.
3. **Remediation:** We develop and validate a security patch, followed by backporting to supported release lines.
4. **Coordinated Disclosure:** We coordinate a public release date with you. We adhere to a 90-day responsible disclosure window before public publication.
5. **Credit:** We acknowledge and credit your contribution in our release notes and GitHub security advisories, unless you prefer to remain anonymous.

## Security Architecture and Scope

UndoLog's security architecture centers on transactional safety for AI tool executions:

- **gRPC Engine Authentication:** Validates token authorization between ingress proxy and core effect state machines.
- **Proxy Rate Limiting and Validation:** Enforces request body limits, API key checks, and concurrency caps before passing commands downstream.
- **Idempotency and Dedup:** Enforces deterministic BLAKE3 signature checks to prevent duplicate tool execution attacks or replay inconsistencies.

### Out of Scope

The following items are outside the scope of our security vulnerability process:

- Vulnerabilities in third-party agent libraries or model providers that do not involve UndoLog wrappers or proxy interceptors.
- Social engineering, phishing, or physical attacks against UndoLog infrastructure or maintainers.
- Volumetric denial of service attacks that exceed configurable proxy limits without exploiting application logic flaws.
