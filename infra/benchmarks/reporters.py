"""Output formatting - JSON files and terminal tables.

Every benchmark run produces:

1. Individual JSON files per benchmark cell for CI diffing.
2. A combined JSON file with all raw data.
3. A human-readable ``summary.txt`` with percentiles and resource metrics.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from infra.benchmarks.harness import BenchResult
from infra.benchmarks.metrics import ResourceCollector

log = logging.getLogger(__name__)


def _results_dir(timestamp: str | None = None) -> Path:
    ts = timestamp or time.strftime("%Y%m%dT%H%M%S")
    path = Path(f"bench-results/{ts}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(
    results: list[BenchResult],
    resources: ResourceCollector | None = None,
    dest: Path | None = None,
) -> Path:
    """Write combined results to a JSON file and per-benchmark JSON files.

    Returns the path to the combined results file.
    """
    ts = time.strftime("%Y%m%dT%H%M%S")
    if dest is None:
        dest_dir = _results_dir(ts)
        dest = dest_dir / "results.json"
    else:
        dest_dir = dest.parent

    # Per-benchmark JSON files.
    for r in results:
        cell_path = dest_dir / f"{r.label}.json"
        cell_data: dict[str, Any] = r.to_dict()
        cell_data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if resources is not None:
            cell_data["resource_snapshots"] = resources.to_dicts()
        cell_path.write_text(json.dumps(cell_data, indent=2))

    # Combined JSON file.
    data: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "benchmarks": [r.to_dict() for r in results],
    }
    if resources is not None:
        data["resource_snapshots"] = resources.to_dicts()
    dest.write_text(json.dumps(data, indent=2))
    log.info("Results written to %s", dest.resolve())
    return dest


def write_summary_txt(
    results: list[BenchResult],
    resources: ResourceCollector | None = None,
    dest_dir: Path | None = None,
) -> Path:
    """Write a human-readable summary.txt file."""
    ts = time.strftime("%Y%m%dT%H%M%S")
    dir_path = dest_dir or _results_dir(ts)
    path = dir_path / "summary.txt"

    lines: list[str] = []
    lines.append("=" * 120)
    lines.append("UndoLog Benchmark Summary")
    lines.append(f"Run at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("=" * 120)

    # Separate overhead benchmarks for delta-from-baseline table.
    overhead_results = [r for r in results if r.label.startswith("overhead-")]
    other_results = [r for r in results if not r.label.startswith("overhead-")]

    if overhead_results:
        lines.append("")
        lines.append("Benchmark 1: Overhead latency (with delta from direct)")
        lines.append("-" * 120)
        lines.append(
            f"{'Tier':<30s} {'Samples':>8s} {'p50(us)':>10s} {'p95(us)':>10s} "
            f"{'p99(us)':>10s} {'Mean(us)':>10s} {'Std(us)':>10s} "
            f"{'Delta p95':>10s}"
        )
        lines.append("-" * 120)

        direct_p95 = next(
            (r.p95_us for r in overhead_results if r.label == "overhead-direct"),
            0.0,
        )

        for r in overhead_results:
            delta = ""
            if r.label != "overhead-direct" and direct_p95 > 0:
                d = ((r.p95_us - direct_p95) / direct_p95) * 100
                delta = f"{d:+.1f}%"
            lines.append(
                f"{r.label:<30s} {r.count:>8d} {r.p50_us:>10.1f} "
                f"{r.p95_us:>10.1f} {r.p99_us:>10.1f} {r.mean_us:>10.1f} "
                f"{r.stddev_us:>10.1f} {delta:>10s}"
            )

    # Main results table.
    if other_results:
        lines.append("")
        lines.append("Benchmark Results")
        lines.append("-" * 120)
        lines.append(
            f"{'Benchmark':<40s} {'Samples':>8s} {'TPS':>8s} {'p50(us)':>10s} "
            f"{'p95(us)':>10s} {'p99(us)':>10s} {'Mean(us)':>10s} "
            f"{'Std(us)':>10s} {'Min(us)':>10s} {'Max(us)':>10s}"
        )
        lines.append("-" * 120)
        for r in other_results:
            tps_str = f"{r.tps:.1f}" if r.tps else "-"
            lines.append(
                f"{r.label:<40s} {r.count:>8d} {tps_str:>8s} "
                f"{r.p50_us:>10.1f} {r.p95_us:>10.1f} {r.p99_us:>10.1f} "
                f"{r.mean_us:>10.1f} {r.stddev_us:>10.1f} "
                f"{r.min_us:>10.1f} {r.max_us:>10.1f}"
            )

    # Flat-line analysis for multi-tenant benchmarks.
    mt_results = [r for r in results if r.label.startswith("multitenant-")]
    if len(mt_results) >= 2:
        lines.append("")
        lines.append("Multi-tenant flat-line analysis")
        lines.append("-" * 60)
        min_p50 = min(r.p50_us for r in mt_results)
        max_p50 = max(r.p50_us for r in mt_results)
        min_p95 = min(r.p95_us for r in mt_results)
        max_p95 = max(r.p95_us for r in mt_results)
        lines.append(f"  p50 range: {min_p50:.1f}us to {max_p50:.1f}us")
        lines.append(f"  p95 range: {min_p95:.1f}us to {max_p95:.1f}us")
        if max_p50 > 0:
            p50_spread_pct = ((max_p50 - min_p50) / min_p50) * 100
            lines.append(
                f"  p50 spread: {p50_spread_pct:.1f}% "
                f"({'FLAT' if p50_spread_pct < 20 else 'GROWTH DETECTED'})"
            )

    # Drift analysis for longevity.
    longevity_results = [r for r in results if r.label == "longevity"]
    if longevity_results:
        lines.append("")
        lines.append("Longevity drift analysis")
        lines.append("-" * 60)
        if resources and len(resources.snapshots) >= 4:
            mid = len(resources.snapshots) // 2
            first_cpu = [
                s.cpu_pct for s in resources.snapshots[:mid] if s.cpu_pct is not None
            ]
            second_cpu = [
                s.cpu_pct for s in resources.snapshots[mid:] if s.cpu_pct is not None
            ]
            first_mem = [
                s.rss_mb for s in resources.snapshots[:mid] if s.rss_mb is not None
            ]
            second_mem = [
                s.rss_mb for s in resources.snapshots[mid:] if s.rss_mb is not None
            ]
            first_gor = [
                s.goroutines
                for s in resources.snapshots[:mid]
                if s.goroutines is not None
            ]
            second_gor = [
                s.goroutines
                for s in resources.snapshots[mid:]
                if s.goroutines is not None
            ]

            if first_cpu and second_cpu:
                cpu_drift = (
                    (
                        (sum(second_cpu) / len(second_cpu))
                        - (sum(first_cpu) / len(first_cpu))
                    )
                    / (sum(first_cpu) / len(first_cpu))
                    * 100
                )
                lines.append(
                    f"  CPU drift: {cpu_drift:+.1f}% "
                    f"({'LEAK' if abs(cpu_drift) > 20 else 'STABLE'})"
                )
            if first_mem and second_mem:
                mem_drift = (
                    (
                        (sum(second_mem) / len(second_mem))
                        - (sum(first_mem) / len(first_mem))
                    )
                    / (sum(first_mem) / len(first_mem))
                    * 100
                )
                lines.append(
                    f"  RSS drift: {mem_drift:+.1f}% "
                    f"({'LEAK' if abs(mem_drift) > 20 else 'STABLE'})"
                )
            if first_gor and second_gor:
                gor_drift = (
                    (
                        (sum(second_gor) / len(second_gor))
                        - (sum(first_gor) / len(first_gor))
                    )
                    / (sum(first_gor) / len(first_gor))
                    * 100
                )
                lines.append(
                    f"  Goroutine drift: {gor_drift:+.1f}% "
                    f"({'LEAK' if abs(gor_drift) > 20 else 'STABLE'})"
                )
        else:
            lines.append("  (insufficient resource snapshots for drift analysis)")

    if resources:
        lines.append("")
        lines.append("Resource Summary")
        lines.append("-" * 60)
        lines.append(resources.summary())

    lines.append("")
    path.write_text("\n".join(lines))
    log.info("Summary written to %s", path.resolve())
    return path


def print_table(
    results: list[BenchResult], resources: ResourceCollector | None = None
) -> None:
    """Print a human-readable results table to stdout."""
    print("")
    print("─" * 120)
    print(
        f"{'Benchmark':<40s} {'Samples':>8s} {'p50(us)':>10s} {'p95(us)':>10s} "
        f"{'p99(us)':>10s} {'Mean(us)':>10s} {'Std(us)':>10s} "
        f"{'Min(us)':>10s} {'Max(us)':>10s}"
    )
    print("─" * 120)
    for r in results:
        print(r.summary())
    print("─" * 120)

    # Overhead delta table.
    overhead_results = [r for r in results if r.label.startswith("overhead-")]
    if overhead_results:
        direct_p95 = next(
            (r.p95_us for r in overhead_results if r.label == "overhead-direct"),
            0.0,
        )
        if direct_p95 > 0:
            print("")
            print("Overhead delta from direct (baseline):")
            for r in overhead_results:
                if r.label == "overhead-direct":
                    continue
                d = ((r.p95_us - direct_p95) / direct_p95) * 100
                print(f"  {r.label:<35s} p95={r.p95_us:>8.1f}us (delta: {d:+.1f}%)")

    # Multi-tenant flat-line check.
    mt_results = [r for r in results if r.label.startswith("multitenant-")]
    if len(mt_results) >= 2:
        min_p50 = min(r.p50_us for r in mt_results)
        max_p50 = max(r.p50_us for r in mt_results)
        if min_p50 > 0:
            spread = ((max_p50 - min_p50) / min_p50) * 100
            status = "FLAT" if spread < 20 else "GROWTH"
            print("")
            print(
                f"Multi-tenant isolation: p50 range {min_p50:.0f}-{max_p50:.0f}us "
                f"({spread:.1f}% spread) - {status}"
            )

    if resources:
        print(resources.summary())
    print("")
