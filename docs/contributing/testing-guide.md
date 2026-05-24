---
title: "Testing guide"
description: "## Running tests"
section: "contributing"
---
# Testing guide

## Running tests

**Rust: all crates:**

```bash
cargo test --workspace
```

This runs unit tests, doc tests, and integration tests across `undolog-engine`, `undolog-store`, `undolog-types`, and `undolog-saga`.

To run a single crate's tests:

```bash
cargo test -p undolog-engine
```

To run a specific test by name:

```bash
cargo test --workspace -- test_name_prefix
```

**Go: proxy service:**

```bash
cd services/undolog-proxy
go test ./... -v -count=1
```

The `-count=1` flag disables test caching. Use `-v` for verbose output, `-run` to filter by test name.

**Python. SDK:**

```bash
cd sdks/undolog-py
pytest -v
```

Uses `pytest-asyncio` for async test support. Tests requiring a running engine use the `engine` fixture defined in `conftest.py`.

## Test database

Integration tests that exercise the PostgreSQL store require a test database. Set the `TEST_DATABASE_URL` environment variable:

```bash
export TEST_DATABASE_URL=postgres://undolog:undolog@localhost:5432/undolog_test
```

The test harness creates the schema on first use and truncates tables between test runs. If you run tests without this variable, tests that require a database are skipped.

## Writing tests

### Naming conventions

| Language | Convention | Example |
|---|---|---|
| Rust | `#[cfg(test)]` module, `fn test_<description>()` | `fn test_compensation_registers_before_execute()` |
| Go | `func Test<Name>(t *testing.T)` | `func TestCompensationRegistersBeforeExecute(t *testing.T)` |
| Python | `def test_<description>()` | `def test_compensation_registers_before_execute()` |

### Asserting error types

**Rust:**

```rust
let err = store.register_effect(&effect).await.unwrap_err();
assert!(matches!(err, StoreError::DuplicateSignature { .. }));
```

**Go:**

```go
err := store.RegisterEffect(ctx, &effect)
var se *StoreError
if errors.As(err, &se) {
    assert.Equal(t, ErrDuplicateSignature, se.Code)
}
```

**Python:**

```python
with pytest.raises(DuplicateSignatureError):
    await store.register_effect(effect)
```

### Mocking the gRPC/proxy boundary

The Go proxy communicates with the Rust engine over gRPC. In Go tests, use a gRPC test server:

```go
func TestProxyRegistersEffect(t *testing.T) {
    srv := startTestEngineServer(t)
    defer srv.Stop()

    client := connectToEngine(t, srv.Addr())
    // test against client
}
```

Define helper functions in a shared `testutil` package to avoid duplicating test server setup across files.

## Testing compensations

Compensation handlers are HTTP endpoints. To test that a compensation is called correctly:

1. Start a local HTTP test server that records incoming requests.
2. Register the compensation through the engine or proxy.
3. Trigger the compensation path.
4. Assert that the test server received the expected request.

**Rust example, using `wiremock` or a simple `actix_web` / `axum` test server:**

```rust
let mock_server = MockServer::start().await;
let uri = mock_server.uri();

// Register compensation pointing at mock_server
engine.register_compensation(effect_id, &uri).await?;

// Trigger compensation
engine.compensate(effect_id).await?;

// Verify mock was called
mock_server.verify().await;
```

## Property-based testing

Use `proptest` in Rust for property-based tests that validate invariants. Property tests are preferred over example-based tests for state machine logic.

```rust
proptest! {
    #[test]
    fn valid_state_transitions_never_produce_invalid_state(
        start in any::<EffectState>(),
        transition in any::<StateTransition>(),
    ) {
        let result = apply_transition(start, transition);
        if let Ok(next) = result {
            prop_assert!(next.is_valid());
        }
    }
}
```

Key invariants to property-test:

- Every valid state transition produces a valid next state
- Compensation is always registered before the action executes
- After crash recovery, the undo stack contains no `running` entries without a corresponding compensation registration
- Advisory lock keys are deterministic across languages for the same input

## Performance tests

Performance benchmarks live in `benches/` directories within each Rust crate. Use `criterion` for microbenchmarks.

Run benchmarks:

```bash
cargo bench --workspace
```

Benchmark guidelines:

- Test at realistic data sizes (not just the happy path with one effect)
- Test concurrent access patterns with multiple goroutines/tasks
- Test crash recovery timing with a realistic undo stack size
- Document the hardware and database configuration in the benchmark output
