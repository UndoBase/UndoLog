#!/usr/bin/env python3
"""UndoLog benchmark runner.

Usage::

    # Run all benchmarks with default settings
    python -m infra.benchmarks.run

    # Run a specific benchmark
    python -m infra.benchmarks.run --benchmark overhead

    # Run with custom concurrency
    python -m infra.benchmarks.run --benchmark throughput --concurrency 10

    # Short run (quick check for CI)
    python -m infra.benchmarks.run --quick

    # Full benchmark suite (long)
    python -m infra.benchmarks.run --benchmark all --warmup 5 --duration 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from infra.benchmarks.harness import BenchResult
from infra.benchmarks.metrics import ResourceCollector
from infra.benchmarks.reporters import print_table, write_json, write_summary_txt
from infra.benchmarks.scenarios import (
    bench_approval_latency,
    bench_compensation_chain,
    bench_dedup,
    bench_longevity,
    bench_multitenant_noise,
    bench_overhead,
    bench_sse_delivery,
    bench_throughput,
)

log = logging.getLogger("bench-runner")

# Resource snapshot interval by benchmark.
_RESOURCE_INTERVALS: dict[str, float] = {
    "overhead": 5.0,
    "throughput": 5.0,
    "dedup": 5.0,
    "compensation": 5.0,
    "multitenant": 5.0,
    "sse": 2.0,
    "approval": 5.0,
    "longevity": 30.0,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UndoLog benchmark runner")
    parser.add_argument(
        "--benchmark",
        choices=[
            "overhead",
            "throughput",
            "dedup",
            "compensation",
            "multitenant",
            "sse",
            "approval",
            "longevity",
            "all",
        ],
        default="all",
        help="Which benchmark to run (default: all)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Concurrency level (default: 1 for most, varies by benchmark)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=5,
        help="Warmup seconds (default: 5)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=15,
        help="Recording duration seconds (default: 15)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Short run for CI (warmup=2, duration=5, concurrency=1)",
    )
    parser.add_argument(
        "--no-resources",
        action="store_true",
        help="Skip resource metric collection",
    )
    return parser.parse_args(argv)


async def _run_all(
    args: argparse.Namespace,
    collector: ResourceCollector | None = None,
) -> list[BenchResult]:
    all_results: list[BenchResult] = []

    if args.benchmark in ("overhead", "all"):
        log.info("=== Benchmark 1: Overhead latency ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["overhead"])
        r = await bench_overhead(
            warmup=args.warmup,
            duration=args.duration,
            concurrency=args.concurrency or 1,
        )
        all_results.extend(r)
        print_table(r)

    if args.benchmark in ("throughput", "all"):
        log.info("=== Benchmark 2: Throughput under concurrency ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["throughput"])
        r = await bench_throughput(
            warmup=args.warmup,
            duration=args.duration if not args.quick else 10,
        )
        all_results.extend(r)
        print_table(r)

    if args.benchmark in ("dedup", "all"):
        log.info("=== Benchmark 3: Dedup overhead ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["dedup"])
        r = await bench_dedup(
            warmup=args.warmup,
            duration=args.duration,
            concurrency=args.concurrency or 1,
        )
        all_results.extend(r)
        print_table(r)

    if args.benchmark in ("compensation", "all"):
        log.info("=== Benchmark 4: Compensation chain latency ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["compensation"])
        r = await bench_compensation_chain(
            warmup=args.warmup,
            duration=args.duration,
        )
        all_results.extend(r)
        print_table(r)

    if args.benchmark in ("multitenant", "all"):
        log.info("=== Benchmark 5: Multi-tenant noise immunity ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["multitenant"])
        r = await bench_multitenant_noise(
            warmup=args.warmup,
            duration=args.duration,
        )
        all_results.extend(r)
        print_table(r)

    if args.benchmark in ("sse", "all"):
        log.info("=== Benchmark 6: SSE event delivery latency ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["sse"])
        # Vary subscriber counts and event rates per the spec.
        subscriber_counts = [1, 5, 10]
        event_rates = [10, 100, 500]
        for sc in subscriber_counts:
            for er in event_rates:
                log.info("  SSE sub=%d rate=%d/s", sc, er)
                r = await bench_sse_delivery(
                    warmup=args.warmup,
                    duration=max(args.duration, 10),
                    subscriber_count=sc,
                    event_rate=er,
                )
                all_results.extend(r)
                print_table(r)

    if args.benchmark in ("approval", "all"):
        log.info("=== Benchmark 7: Approval workflow latency ===")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["approval"])
        r = await bench_approval_latency(
            warmup=args.warmup,
            duration=args.duration,
            concurrency=args.concurrency or 1,
        )
        all_results.extend(r)
        print_table(r)

    if args.benchmark in ("longevity", "all"):
        log.info("=== Benchmark 8: Longevity (resource leak detection) ===")
        log.warning("Longevity runs for 30 minutes at concurrency=10 by default")
        if collector is not None:
            collector.set_interval(_RESOURCE_INTERVALS["longevity"])
        r = await bench_longevity(
            warmup=30,
            duration=1800,
            concurrency=args.concurrency or 10,
        )
        all_results.extend(r)
        print_table(r)

    return all_results


async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.quick:
        args.warmup = 2
        args.duration = 5
        args.concurrency = 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.no_resources:
        results = await _run_all(args)
    else:
        collector = ResourceCollector(interval=5.0)
        async with collector:
            results = await _run_all(args, collector)
        print_table(results, collector)
        write_json(results, collector)
        write_summary_txt(results, collector)
        return

    print_table(results)
    write_json(results)
    write_summary_txt(results)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
