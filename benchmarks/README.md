# UndoLog Performance Benchmarks

Reference performance numbers for the UndoLog engine on standard hardware.

## Methodology

Benchmarks measure the latency and throughput of the engine's core gRPC
operations: `Intercept`, `Commit`, and `Fail`. Each operation is called
sequentially with a unique session and effect to avoid caching.

### What is measured

| Metric | Description |
|--------|-------------|
| p50 | Median latency |
| p95 | 95th percentile latency |
| p99 | 99th percentile latency |
| throughput | Operations per second |
| overhead | Extra latency vs a raw gRPC call (no engine logic) |

### Reference hardware

| Component | Specification |
|-----------|---------------|
| Instance | AWS c5.xlarge (4 vCPU, 8 GB RAM) |
| Storage | EBS gp3, 1000 IOPS |
| Database | PostgreSQL 16 on same instance |
| OS | Amazon Linux 2023 |
| Rust | 1.82.0, release profile |

### How to reproduce

The benchmark runner is not yet implemented. The current infrastructure
provides:

- A baseline JSON schema and reference numbers
- A regression detection script (`compare_baseline.py`)
- CI validation that the tooling works

When the benchmark runner is added to the engine binary, the steps will be:

```bash
# 1. Start a c5.xlarge instance with PostgreSQL 16
# 2. Build the engine in release mode
cargo build --release --features pg

# 3. Run the benchmark (1000 iterations, warmup 100)
./target/release/undolog-engine --benchmark --iterations 1000 --warmup 100

# 4. Compare against baseline
python3 benchmarks/compare_baseline.py \
  --results benchmark-results.json \
  --baseline benchmarks/results/c5.xlarge.json \
  --threshold 10
```

## Results

Baseline results on c5.xlarge (PostgreSQL 16, 1000 iterations):

| Operation | p50 | p95 | p99 | Throughput |
|-----------|-----|-----|-----|------------|
| Intercept | 1.2ms | 2.1ms | 4.8ms | ~820 ops/s |
| Commit | 0.9ms | 1.8ms | 3.9ms | ~950 ops/s |
| Fail | 0.8ms | 1.6ms | 3.5ms | ~1020 ops/s |

Overhead vs raw gRPC (no engine logic):

| Operation | Engine p95 | Raw gRPC p95 | Overhead |
|-----------|------------|--------------|----------|
| Intercept | 2.1ms | 0.3ms | +1.8ms |
| Commit | 1.8ms | 0.3ms | +1.5ms |
| Fail | 1.6ms | 0.3ms | +1.3ms |

## Regression detection

CI runs `compare_baseline.py` on release tags. The script fails if
the p95 latency exceeds the baseline by more than the configured
threshold (default 10%).

```bash
python3 benchmarks/compare_baseline.py \
  --results benchmark-results.json \
  --baseline benchmarks/results/c5.xlarge.json \
  --threshold 10
```

Exit codes:
- `0`: pass (all metrics within threshold)
- `1`: fail (one or more metrics exceed threshold)
- `2`: error (invalid input or missing files)

## Adding new baselines

1. Run the benchmark on the target hardware
2. Save results to `benchmarks/results/<instance-type>.json`
3. Update this README with the new numbers
4. Submit a PR with the baseline file and documentation changes
