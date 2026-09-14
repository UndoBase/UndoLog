#!/usr/bin/env python3
"""Compare benchmark results against a stored baseline.

Compares p95 latency for each operation (intercept, commit, fail)
against a baseline JSON file. Fails if any p95 exceeds the baseline
by more than the configured threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    """Load and parse a JSON file."""
    with path.open() as f:
        return json.load(f)


def compare(results: dict, baseline: dict, threshold: float) -> list[str]:
    """Compare results against baseline. Returns list of failure messages."""
    failures: list[str] = []

    for operation in ("intercept", "commit", "fail"):
        if operation not in results.get("results", {}):
            failures.append(f"missing operation '{operation}' in results")
            continue
        if operation not in baseline.get("results", {}):
            failures.append(f"missing operation '{operation}' in baseline")
            continue

        result_p95 = results["results"][operation].get("p95_us")
        baseline_p95 = baseline["results"][operation].get("p95_us")

        if result_p95 is None or baseline_p95 is None:
            failures.append(f"missing p95_us for operation '{operation}'")
            continue

        if baseline_p95 == 0:
            failures.append(f"baseline p95 is zero for '{operation}'")
            continue

        change_pct = ((result_p95 - baseline_p95) / baseline_p95) * 100

        if change_pct > threshold:
            failures.append(
                f"{operation}: p95 {result_p95}us exceeds baseline "
                f"{baseline_p95}us by {change_pct:.1f}% (threshold {threshold}%)"
            )

    return failures


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Compare benchmark results against a baseline."
    )
    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="Path to benchmark results JSON file.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to baseline JSON file.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=10.0,
        help="Maximum allowed percentage increase in p95 latency (default: 10).",
    )
    args = parser.parse_args(argv)

    if not args.results.exists():
        print(f"error: results file not found: {args.results}", file=sys.stderr)
        return 2
    if not args.baseline.exists():
        print(f"error: baseline file not found: {args.baseline}", file=sys.stderr)
        return 2

    try:
        results = load_json(args.results)
        baseline = load_json(args.baseline)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 2

    failures = compare(results, baseline, args.threshold)

    if failures:
        print("FAIL: benchmark regression detected")
        for msg in failures:
            print(f"  - {msg}")
        return 1

    print("PASS: all metrics within threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
