#!/usr/bin/env python3
"""Compare benchmark results against a baseline and detect regressions.

Usage::

    python infra/benchmarks/compare_regression.py \\
        --current bench-results/20260710T120000/results.json \\
        --baseline bench-results/20260709T120000/results.json \\
        --threshold 20
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("regression-check")


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        log.error("File not found: %s", path)
        sys.exit(1)
    with open(path) as f:
        return dict(json.load(f))


def _find_baseline() -> Path | None:
    """Find the most recent results directory as a baseline.

    Looks in ``bench-results/`` for the latest timestamped directory
    that is not the current run (assumes *current* is still writing).
    """
    base = Path("bench-results")
    if not base.is_dir():
        return None
    dirs = sorted(base.iterdir(), reverse=True)
    return dirs[1] / "results.json" if len(dirs) >= 2 else None


def print_comparison(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    threshold_pct: float = 20.0,
) -> int:
    """Compare p95 values, print a comparison table, and return exit code."""
    current_benches = {b["benchmark"]: b for b in current.get("benchmarks", [])}
    baseline_benches = (
        {b["benchmark"]: b for b in baseline.get("benchmarks", [])} if baseline else {}
    )

    if not current_benches:
        log.warning("No benchmark results in current run.")
        return 0

    if not baseline_benches:
        log.warning("No baseline available - recording as new baseline.")
        return 0

    regressions: list[str] = []

    print("")
    print("─" * 80)
    print(
        f"{'Benchmark':<40s} {'Current p95':>12s} {'Baseline p95':>12s} {'Change':>10s}"
    )
    print("─" * 80)

    for name, cur in sorted(current_benches.items()):
        cur_p95 = cur.get("latency_us", {}).get("p95", 0)
        bl = baseline_benches.get(name)
        bl_p95 = bl.get("latency_us", {}).get("p95", 0) if bl else 0

        if bl_p95 > 0 and cur_p95 > 0:
            change_pct = ((cur_p95 - bl_p95) / bl_p95) * 100
        else:
            change_pct = 0.0

        change_str = f"{change_pct:+.1f}%"

        if change_pct > threshold_pct:
            regressions.append(
                f"{name}: {cur_p95:.1f}us vs baseline {bl_p95:.1f}us "
                f"({change_pct:+.1f}%, threshold {threshold_pct:+.0f}%)"
            )

        print(f"{name:<40s} {cur_p95:>12.1f}us {bl_p95:>12.1f}us {change_str:>10s}")

    print("─" * 80)

    if regressions:
        log.error("REGRESSIONS DETECTED:")
        for r in regressions:
            log.error("  %s", r)
        return 1

    log.info("No regressions detected (threshold: %.0f%%).", threshold_pct)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark regression checker")
    parser.add_argument(
        "--current", required=True, type=Path, help="Current results.json"
    )
    parser.add_argument(
        "--baseline", type=Path, default=None, help="Baseline results.json"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=20.0,
        help="P95 regression threshold in percent (default: 20)",
    )
    args = parser.parse_args()

    current = _load(args.current)
    baseline = _load(args.baseline) if args.baseline else None
    if baseline is None and args.baseline is None:
        baseline = _load(_find_baseline()) if _find_baseline() else None  # type: ignore[arg-type]
        if baseline:
            log.info("Using auto-detected baseline: %s", _find_baseline())

    rc = print_comparison(current, baseline, args.threshold)
    sys.exit(rc)


if __name__ == "__main__":
    main()
