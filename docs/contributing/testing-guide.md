---
title: "Testing guide"
description: "How to run the Rust, Go, Python, and example test suites, and which tests need a live stack."
section: "contributing"
---
# Testing guide

## Running tests

**Rust: all crates:**

```bash
cargo test --lib --workspace --exclude undolog-engine
```

This runs unit tests across `undolog-store`, `undolog-types`, and `undolog-saga` without requiring a database. This command runs as part of `make check`.

To run tests across a specific crate:

```bash
cargo test -p undolog-engine --lib
```

To run a specific test by name:

```bash
cargo test --workspace -- test_name_prefix
```

**Go: proxy service:**

```bash
cd services/undolog-proxy
go test -race ./... -v -count=1
```

The `-count=1` flag disables test caching. Use `-v` for verbose output, `-run` to filter by test name, and `-race` to run under the Go race detector. Running with `-race` is the default in `make test-go` and CI.

The proxy tests need no Docker, no PostgreSQL, and no listening ports: the engine gRPC boundary is served in process over a `bufconn` listener (`internal/proxy/harness_test.go`), so the full stack, proto marshaling included, runs inside `go test`.

The `internal/proxy` package carries an automated statement coverage gate:

```bash
make coverage-go
```

The target fails if statement coverage for `internal/proxy` falls below 80 percent. The target writes `coverage.out` in `services/undolog-proxy/`.

To inspect line-by-line coverage in a web browser:

```bash
cd services/undolog-proxy
go tool cover -html=coverage.out
```

To view package and function coverage summaries in your terminal:

```bash
cd services/undolog-proxy
go tool cover -func=coverage.out
```

**Python. SDK:**

```bash
cd sdks/undolog-py
pytest -v
```

Uses `pytest-asyncio` for async test support. Tests requiring a running engine use the `engine` fixture defined in `conftest.py`.

## Test database and engine tiers

Integration tests that exercise the PostgreSQL store require a test database. Set the `TEST_DATABASE_URL` environment variable:

```bash
export TEST_DATABASE_URL=postgres://undolog:undolog@localhost:5432/undolog_test
```

The test harness creates the schema on first use and truncates tables between test runs.

### Engine test tiers

The Rust engine workspace separates tests into five distinct tiers based on external dependencies and verification scope:

- **Tier 1: Unit and library tests:** Fast, in-process tests covering core types, serialization, and state helpers without database dependencies:

```bash
cargo test --lib --workspace --exclude undolog-engine
```

- **Tier 2: Property-based tests:** Invariant verification using `proptest` across generated inputs. These tests validate signature determinism, canonical JSON sorting, and state transitions without opening network or database connections:

```bash
cargo test -p undolog-engine --test property_tests
cargo test -p undolog-saga --test saga_property_tests
```

- **Tier 3: Feature-gated SQLite tests:** Storage layer tests running against SQLite in `:memory:` without PostgreSQL:

```bash
cargo test -p undolog-store --features sqlite --test test_sqlite
```

- **Tier 4: Chaos engineering tests:** Resilience tests under adverse failure conditions (`crates/undolog-engine/tests/chaos.rs`). Without `TEST_DATABASE_URL`, tests requiring PostgreSQL are ignored, while offline failure tests run:

```bash
# Run offline chaos tests (skips DB-dependent cases):
cargo test -p undolog-engine --test chaos

# Run all chaos tests against PostgreSQL:
cargo test -p undolog-engine --test chaos -- --ignored
```

- **Tier 5: Integration tests (PostgreSQL):** End-to-end routing, approval timeouts, backpressure, dead-letter administration, and saga crash recovery. These tests require `TEST_DATABASE_URL`:

```bash
cargo test -p undolog-saga --test saga_integration_tests
cargo test -p undolog-engine --test integration_tests -- --ignored
cargo test -p undolog-engine --test test_timeout
cargo test -p undolog-engine --test test_rate_limit -- --ignored
cargo test -p undolog-engine --test test_admin_api -- --ignored
```

### Database gating mechanism

The engine crates gate database tests using two complementary mechanisms:

- **Ignored tests in `undolog-engine`:** Integration and chaos tests that connect to PostgreSQL use `#[ignore = "requires TEST_DATABASE_URL"]`. Running `cargo test` executes the offline tests without failing, allowing offline builds and fast local iteration. Passing `-- --ignored` instructs the test runner to execute the database suite.
- **Environment assertion in `undolog-saga`:** Saga integration tests (`crates/undolog-saga/tests/saga_integration_tests.rs`) verify crash recovery, compensation retries, and LIFO ordering against live PostgreSQL instances, requiring `TEST_DATABASE_URL` to be explicitly set.

### Feature-gated test split

The engine and store crates support two storage backends controlled by feature flags:

- **`sqlite` feature:** Compiles SQLite support using `sqlx` in WAL mode with async connection pooling. To build and test with SQLite:

```bash
cargo test -p undolog-store --features sqlite --test test_sqlite
cargo build -p undolog-engine --no-default-features --features sqlite
```

- **`pg` feature:** Enables PostgreSQL support (enabled by default in `undolog-engine`). To compile explicitly with PostgreSQL:

```bash
cargo build -p undolog-engine --features pg
```

### Chaos engineering scenarios

The chaos test suite (`crates/undolog-engine/tests/chaos.rs`) simulates five specific operational failure modes:

1. **Crash during compensation:** Aborts an active tokio task mid-intercept to simulate an unexpected process crash. Asserts that the session record remains in a valid state (`active`, `compensating`, `compensated`, `failed`, `halted`) with zero database corruption.
2. **Concurrent same-signature serialization:** Dispatches two concurrent intercepts with identical parameters for the same session and step. Asserts that PostgreSQL advisory locks serialize execution so exactly one returns `Execute` and the other returns `Replay`.
3. **Database connection failure:** Configures an unreachable database endpoint. Asserts that the engine cleanly propagates connection errors without panicking.
4. **Pre-existing session recovery:** Creates session rows directly in the database, then starts a fresh engine instance with empty in-memory caches. Asserts that the engine processes intercepts correctly against existing state.
5. **Rapid repeated intercepts:** Fires a burst of 10 concurrent requests with identical signatures. Asserts that exactly one `Execute` and nine `Replay` outcomes are produced, verifying idempotency under high contention.

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

### In-process gRPC engine harness

The Go proxy communicates with the Rust engine over gRPC. Rather than mocking network boundaries or requiring a live engine process, proxy unit tests serve a scripted engine in process using the test harness in `services/undolog-proxy/internal/proxy/harness_test.go`.

The harness pairs two components:

- **`scriptedEngine`:** Implements the generated `pb.UndoLogEngineServer` interface with canned answers (`interceptResp`, `interceptErr`, `commitErr`, `approveResp`). It records every incoming request (`interceptReq`, `commitReq`, `approveReq`), allowing tests to assert on the exact protobuf payload sent across the wire.
- **`newEngineHarness`:** Starts the scripted engine on an in-memory `bufconn` listener (`1 << 20` byte buffer) and dials it with the production `engine.Client`. This exercises the real gRPC codec, protobuf serialization, and status error mapping without opening network ports, running Docker, or needing PostgreSQL. Teardown of the client, gRPC connection, server, and listener is handled automatically via `t.Cleanup`.

Tests construct the proxy server via `newProxyTestServer(t, harness.client, executor)` and invoke requests directly using `server.httpSrv.Handler.ServeHTTP(rec, req)`.

Here is a complete test pattern demonstrating intercept execution and request assertion:

```go
func TestInterceptExecutesToolWithHarness(t *testing.T) {
	// Script the engine to return an Execute outcome.
	script := &scriptedEngine{
		interceptResp: &pb.InterceptResponse{
			Outcome: &pb.InterceptResponse_Execute{
				Execute: &pb.ExecuteOutcome{EffectId: "eff-1"},
			},
		},
	}
	harness := newEngineHarness(t, script)

	execCalls := 0
	server := newProxyTestServer(t, harness.client, ToolExecutorFunc(func(_ context.Context, call protocol.ToolCall) (protocol.ToolResult, error) {
		execCalls++
		if call.ToolName != "search" {
			t.Errorf("executed tool = %q, want search", call.ToolName)
		}
		return protocol.ToolResult{
			Success: true,
			Output:  json.RawMessage(`{"found":true}`),
		}, nil
	}))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(
		http.MethodPost,
		"/mcp/tool_call",
		strings.NewReader(`{"session_id":"s1","tool_name":"search","args":{"query":"test"}}`),
	)
	req.Header.Set("X-Api-Key", "key-1")
	server.httpSrv.Handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (%s)", rec.Code, rec.Body.String())
	}
	if execCalls != 1 {
		t.Fatalf("executor calls = %d, want 1", execCalls)
	}
	if script.interceptReq == nil || script.interceptReq.ToolName != "search" {
		t.Fatalf("unexpected intercept request recorded by engine: %+v", script.interceptReq)
	}
}
```

To test engine failure modes, script errors directly onto `scriptedEngine`:

```go
// Test that engine transient failure returns 502 Bad Gateway with gRPC error message:
script := &scriptedEngine{
	interceptErr: status.Error(codes.Unavailable, "engine restarting"),
}
harness := newEngineHarness(t, script)
server := newProxyTestServer(t, harness.client, executor)
```

### Startup and concurrency test patterns

The proxy service coordinates concurrent requests, background approval reconciliation, configuration reloads, and SSE broadcasts across shared state. Concurrency tests in `services/undolog-proxy/internal/proxy/` follow three established patterns:

- **Simultaneous release via an unbuffered channel:** Tests spin up worker goroutines that increment a `sync.WaitGroup` and immediately block on `<-start`. Closing `close(start)` releases all workers at the same instant, maximizing lock contention and scheduling overlap:

```go
const workers = 16
var wg sync.WaitGroup
start := make(chan struct{})

for i := 0; i < workers; i++ {
	wg.Add(1)
	go func() {
		defer wg.Done()
		<-start
		// Execute concurrent operation under test
	}()
}
close(start)
wg.Wait()
```

- **Race detector as assertion:** All proxy tests run under `go test -race` (via `make test-go` and CI). In concurrency tests such as `TestStartupPassRacesSafely` and `TestWatcherReloadsConfigConcurrentlyWithInFlightRequests`, the primary assertion is that the race detector detects zero unsynchronized memory accesses while state transitions run concurrently.
- **Settled state and invariant verification:** Once `wg.Wait()` returns, tests assert that shared structures settle into consistent states. For example, verifying that SSE subscriber gauges settle back to zero after concurrent unsubscribes, that the approval store contains reconciled records, or that burst requests receive only valid status codes (such as `200` or `503 Service Unavailable` with `Retry-After`).

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
