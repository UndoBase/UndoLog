# UndoLog - proto code generation
#
# The gRPC contract lives in proto/undolog.proto (single source of truth).
# Generated stubs:
#   Rust: crates/undolog-engine/src/grpc/  (via tonic-build in build.rs)
#   Go:   services/undolog-proxy/internal/engine/pb/  (via protoc-gen-go-grpc)

GO_PB_DIR := services/undolog-proxy/internal/engine/pb

.PHONY: proto proto-go proto-rust clean check fmt lint test typecheck test-all demo-multi-tenant

proto: proto-go proto-rust

# ── Go stubs ──────────────────────────────────────────────────────────────

proto-go: proto/undolog.proto
	protoc \
		--proto_path=proto \
		--go_out=$(GO_PB_DIR) --go_opt=paths=source_relative \
		--go-grpc_out=$(GO_PB_DIR) --go-grpc_opt=paths=source_relative \
		proto/undolog.proto
	@echo "Go stubs generated in $(GO_PB_DIR)"

# ── Rust stubs ────────────────────────────────────────────────────────────
# Rust stubs are generated at compile time by crates/undolog-engine/build.rs
# via tonic-build.  Run this target to regenerate them manually if needed.

proto-rust:
	cargo build -p undolog-engine
	@echo "Rust stubs regenerated (via tonic-build in build.rs)"

# ── CI checks ─────────────────────────────────────────────────────────────
# Run `make check` before committing to verify all CI checks pass locally.
# Note: Database-dependent integration tests require TEST_DATABASE_URL.
# Run those separately: cargo test -p undolog-saga --test saga_integration_tests

check: fmt lint test

fmt:
	cargo fmt --all --check
	cd services/undolog-proxy && test -z "$$(gofmt -l .)"
	ruff format --check sdks/undolog-py/.

lint:
	cargo clippy --all-targets --all-features
	cd services/undolog-proxy && go vet ./...
	ruff check sdks/undolog-py/.

test:
	cargo test --lib --workspace --exclude undolog-engine
	cd services/undolog-proxy && go test ./... -count=1
	cd sdks/undolog-py && python -m pytest -v
	cd apps/www && npm run build

typecheck:
	cd sdks/undolog-py && mypy undolog_sdk/ tests/

test-examples:
	@for dir in examples/*-support-agent; do \
		echo "=== Testing $$dir ==="; \
		(cd "$$dir" && python -m pytest -v --no-header -m "not integration") || true; \
	done

test-mock-server:
	python -m pytest infra/mock-tool-server/tests/ -v --no-header

demo-multi-tenant:
	python examples/langchain-support-agent/multi_tenant_demo.py

test-all: check test-examples test-mock-server

# ── Clean ─────────────────────────────────────────────────────────────────

clean:
	rm -f $(GO_PB_DIR)/undolog.pb.go $(GO_PB_DIR)/undolog_grpc.pb.go
	cargo clean
