# UndoLog benchmark targets.
#
# Usage:
#   make bench                  # Run all benchmarks (full)
#   make bench-quick            # Quick CI check
#   make bench-overhead         # Overhead latency only
#   make bench-throughput       # Throughput only
#   make bench-dedup            # Dedup only
#   make bench-compensation     # Compensation chain latency
#   make bench-multitenant      # Multi-tenant noise immunity
#   make bench-sse              # SSE delivery latency
#   make bench-approval         # Approval workflow latency
#   make bench-longevity        # 30-min longevity test

.PHONY: bench bench-quick bench-overhead bench-throughput bench-dedup \
        bench-compensation bench-multitenant bench-sse bench-approval bench-longevity

bench:
	python -m infra.benchmarks.run --warmup 5 --duration 15

bench-quick:
	python -m infra.benchmarks.run --quick

bench-overhead:
	python -m infra.benchmarks.run --benchmark overhead --warmup 5 --duration 15

bench-throughput:
	python -m infra.benchmarks.run --benchmark throughput --warmup 5 --duration 30

bench-dedup:
	python -m infra.benchmarks.run --benchmark dedup --warmup 5 --duration 15

bench-compensation:
	python -m infra.benchmarks.run --benchmark compensation --warmup 5 --duration 20

bench-multitenant:
	python -m infra.benchmarks.run --benchmark multitenant --warmup 5 --duration 20

bench-sse:
	python -m infra.benchmarks.run --benchmark sse --warmup 5 --duration 20

bench-approval:
	python -m infra.benchmarks.run --benchmark approval --warmup 5 --duration 20

bench-longevity:
	python -m infra.benchmarks.run --benchmark longevity --warmup 30 --duration 1800
