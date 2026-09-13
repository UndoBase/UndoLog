---
title: "Compatibility Matrix"
description: "SDK and engine version compatibility for UndoLog."
section: "reference"
---
# Compatibility Matrix

SDK and engine version compatibility for UndoLog.

---

## Version compatibility

UndoLog follows semantic versioning. The engine and SDKs are versioned
independently. This matrix shows which SDK versions work with which
engine versions.

### Python SDK

| SDK Version | Engine Version | Status |
|-------------|----------------|--------|
| 0.1.x | 0.1.x | Supported |
| 0.2.x | 0.1.x | Supported |
| 0.2.x | 0.2.x | Supported |

### TypeScript SDK

| SDK Version | Engine Version | Status |
|-------------|----------------|--------|
| 0.1.x | 0.1.x | Supported |
| 0.2.x | 0.1.x | Supported |
| 0.2.x | 0.2.x | Supported |

### Go Proxy

| Proxy Version | Engine Version | Status |
|---------------|----------------|--------|
| 0.1.x | 0.1.x | Supported |
| 0.2.x | 0.1.x | Supported |
| 0.2.x | 0.2.x | Supported |

---

## Compatibility rules

1. **Patch versions are always compatible.** SDK 0.1.0 works with engine
   0.1.5. Engine 0.1.0 works with SDK 0.1.3.

2. **Minor versions within 0.x are compatible.** SDK 0.1.x works with
   engine 0.2.x if no breaking protobuf changes were introduced.

3. **Major versions require matching major versions.** SDK 1.0 requires
   engine 1.0 or later.

4. **gRPC protocol version must match.** The engine exposes
   `undolog.v1` in the proto package. A future `undolog.v2` proto would
   require SDK updates.

---

## Breaking changes

A breaking change is any modification to:

- gRPC service definitions or message types
- Effect state machine transitions
- Approval workflow semantics
- Environment variable names or defaults
- HTTP API request/response formats

Breaking changes are only introduced in major version bumps. Between
major versions, the engine maintains backward compatibility with the
previous two minor versions of each SDK.

---

## Upgrade path

When upgrading across minor versions:

1. Check the changelog for breaking changes
2. Upgrade the engine first (it is backward compatible with older SDKs)
3. Upgrade SDKs after the engine is stable
4. Run the integration test suite to verify

When upgrading across major versions:

1. Read the migration guide in the changelog
2. Upgrade the engine and all SDKs together
3. Update any custom tool registrations or tier configurations
4. Run the full test suite

---

## See also

- [Deprecation policy](deprecation-policy.md): timeline for deprecated features
- [Configuration reference](configuration.md): environment variables
- [Error codes](error-codes.md): gRPC and HTTP error codes
