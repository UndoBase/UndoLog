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
| Go | 1.22+ | Build the proxy service |
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

## Database setup

### Option A: Docker Compose (recommended)

```bash
docker compose up -d postgres
```

The PostgreSQL container runs migrations automatically on startup. The default connection string is `postgres://undolog:undolog@localhost:5432/undolog_dev`.

### Option B: Manual

```bash
createdb undolog_dev
psql undolog_dev -f migrations/0001_initial.sql
```

If you need additional migrations, apply them in order:

```bash
for f in migrations/*.sql; do psql undolog_dev -f "$f"; done
```

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

## Run the engine

```bash
cargo run -p undolog-engine
```

The engine starts a tonic gRPC server on `0.0.0.0:50051`. You should see log output confirming the server is listening.

## Run the proxy

Open a second terminal:

```bash
cd services/undolog-proxy
go run ./cmd/proxy
```

The proxy starts an HTTP server on `0.0.0.0:8080`.

## Verify

Check health endpoints from a third terminal:

```bash
# Proxy health
curl localhost:8080/health
# Expected: {"status":"ok"}

# Engine gRPC (requires grpcurl)
grpcurl -plaintext localhost:50051 list
# Expected: undolog.v1.EngineService
```

## Troubleshooting

| Problem | Likely cause | Solution |
|---|---|---|
| `cargo build` fails with "package not found" | Rust toolchain too old | Run `rustup update stable` |
| `go build` fails with missing module | Go proxy submodule not initialised | Run `cd services/undolog-proxy && go mod download` |
| Engine can't connect to PostgreSQL | Database not running or wrong URL | Check `docker ps`, verify `DATABASE_URL` in `.env` |
| `pip install -e .[dev]` fails | Shell expands `[dev]` as glob | Quote the path: `pip install -e ".[dev]"` |
| Go proxy can't connect to engine | Engine not running | Start the engine in another terminal |
| `protoc` not found when building | `protoc` not installed or not in `$PATH` | Install from https://protobuf.dev/downloads/ |
| Dashboard `npm run dev` fails | Node.js version mismatch | Use `nvm use 22` or upgrade Node.js |
| Migration errors | Already-applied migration | Run `psql undolog_dev -c "DROP TABLE IF EXISTS _sqlx_migrations CASCADE;"` and re-run |
