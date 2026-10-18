---
title: "Development setup"
description: "This guide walks through setting up a local UndoLog development environment from scratch."
section: "contributing"
---
# Development setup

This guide walks through setting up a local UndoLog development environment from scratch.

## Prerequisites

| Tool | Minimum version | Why |
|---|---|---|
| Docker | 24+ | PostgreSQL and full-stack Compose environment |
| Rust | 1.87+ | Build the engine, store, types, and saga crates |
| Go | 1.25+ | Build the proxy service |
| Python | 3.10+ | Install and develop the Python SDK |
| Node.js | 22+ | Run the Next.js dashboard |
| protoc | 25+ | Generate protobuf stubs |

Verify your installations:

```bash
rustc --version
go version
python3 --version
node --version
protoc --version
docker --version
```

## Clone and build

```bash
git clone https://github.com/your-org/undolog.git
cd undolog
```

**Rust crates:**

```bash
cargo build --workspace
```

This builds all four crates: `undolog-engine`, `undolog-store`, `undolog-types`, `undolog-saga`.

**Go proxy:**

```bash
cd services/undolog-proxy
go build ./cmd/proxy
cd ../..
```

**Python SDK (editable install):**

```bash
cd sdks/undolog-py
pip install -e .[dev]
cd ../..
```

**Dashboard:**

```bash
cd apps/dashboard
npm install
cd ../..
```

## Database setup and migrations

### Option A: Standalone migration CLI (recommended)

Apply migrations using the standalone migration tool:

```bash
cargo run -p undolog-migrate -- up
```

To inspect migration status:

```bash
cargo run -p undolog-migrate -- status
```

### Option B: Docker Compose

Start PostgreSQL via Docker Compose:

```bash
docker compose up -d postgres
```

The PostgreSQL container runs migrations automatically on startup. The default connection string is `postgres://undolog:undolog@localhost:5432/undolog_dev`.

## Environment

Copy the example environment file and adjust as needed:

```bash
cp .env.example .env
```

The default values work for local development:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgres://undolog:undolog@localhost:5432/undolog_dev` | PostgreSQL connection string |
| `ENGINE_PORT` | `50051` | gRPC port for the engine |
| `PROXY_PORT` | `8080` | HTTP port for the proxy |
| `RUST_LOG` | `info` | Engine log level |
| `TEST_DATABASE_URL` | (see `.env.example`) | Used by integration tests |
| `UNDOLOG_SQLITE_PATH` | `:memory:` | SQLite path when running the sqlite engine |

## Local development loops

UndoLog supports four development workflows depending on your focus:

### 1. Zero-infrastructure loop with undolog dev

For Python SDK development, framework wrappers, and test applications, run the embedded development server:

```bash
undolog dev
```

This starts a local development server with SQLite storage, requiring no Docker, no PostgreSQL, and no compilation step.

### 2. In-memory SQLite engine loop

To develop against the Rust engine without running PostgreSQL, build and run with the `sqlite` feature flag:

```bash
cargo run -p undolog-engine --no-default-features --features sqlite
```

### 3. Combined one-container loop

To run the proxy and engine together in a single container alongside PostgreSQL:

```bash
docker compose -f docker-compose.yml -f infra/docker-compose.single.yml up -d
```

Inside the container, the proxy reaches the engine on `127.0.0.1:50051`.

### 4. Standard split-stack loop

Run PostgreSQL in Docker, then launch the engine and proxy in separate terminals:

```bash
# Terminal 1: engine
cargo run -p undolog-engine

# Terminal 2: proxy
cd services/undolog-proxy
go run ./cmd/proxy
```

## Verify

Check health endpoints from a third terminal:

```bash
# Proxy health
curl localhost:8080/health
# Expected: {"status":"ok","service":"undolog-proxy"}

# Engine gRPC (requires grpcurl)
grpcurl -plaintext localhost:50051 list
# Expected: undolog.v1.UndoLogEngine
```

## Troubleshooting

| Problem | Likely cause | Solution |
|---|---|---|
| `cargo build` fails with "package not found" | Rust toolchain too old | Run `rustup update` to refresh the pinned toolchain |
| `go build` fails with missing module | Go proxy submodule not initialised | Run `cd services/undolog-proxy && go mod download` |
| Engine can't connect to PostgreSQL | Database not running or wrong URL | Check `docker ps`, verify `DATABASE_URL` in `.env` |
| `pip install -e .[dev]` fails | Shell expands `[dev]` as glob | Quote the path: `pip install -e ".[dev]"` |
| Go proxy can't connect to engine | Engine not running | Start the engine in another terminal |
| `protoc` not found when building | `protoc` not installed or not in `$PATH` | Install from https://protobuf.dev/downloads/ |
| Dashboard `npm run dev` fails | Node.js version mismatch | Use `nvm use 22` or upgrade Node.js |
| Migration errors | Already-applied migration | Run `psql undolog_dev -c "DROP TABLE IF EXISTS _sqlx_migrations CASCADE;"` and re-run |
