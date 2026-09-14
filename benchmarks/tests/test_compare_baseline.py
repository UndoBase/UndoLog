#!/usr/bin/env python3
"""Tests for compare_baseline.py."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from compare_baseline import compare, main


def _write_json(data: dict) -> Path:
    """Write a dict to a temporary JSON file and return the path."""
    fd, path_str = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    p = Path(path_str)
    p.write_text(json.dumps(data))
    return p


BASELINE = {
    "metadata": {"instance_type": "c5.xlarge"},
    "results": {
        "intercept": {"p50_us": 1200, "p95_us": 2100, "p99_us": 4800},
        "commit": {"p50_us": 900, "p95_us": 1800, "p99_us": 3900},
        "fail": {"p50_us": 800, "p95_us": 1600, "p99_us": 3500},
    },
}


def test_pass_within_threshold() -> None:
    """Results within threshold should pass."""
    results = {
        "results": {
            "intercept": {"p95_us": 2200},
            "commit": {"p95_us": 1850},
            "fail": {"p95_us": 1650},
        }
    }
    failures = compare(results, BASELINE, threshold=10.0)
    assert failures == []


def test_fail_exceeds_threshold() -> None:
    """Results exceeding threshold should fail."""
    results = {
        "results": {
            "intercept": {"p95_us": 3000},
            "commit": {"p95_us": 1800},
            "fail": {"p95_us": 1600},
        }
    }
    failures = compare(results, BASELINE, threshold=10.0)
    assert len(failures) == 1
    assert "intercept" in failures[0]


def test_fail_multiple_operations() -> None:
    """Multiple operations exceeding threshold should all fail."""
    results = {
        "results": {
            "intercept": {"p95_us": 3000},
            "commit": {"p95_us": 3000},
            "fail": {"p95_us": 3000},
        }
    }
    failures = compare(results, BASELINE, threshold=10.0)
    assert len(failures) == 3


def test_missing_operation() -> None:
    """Missing operation in results should fail."""
    results = {"results": {"intercept": {"p95_us": 2100}}}
    failures = compare(results, BASELINE, threshold=10.0)
    assert len(failures) == 2
    assert any("commit" in f for f in failures)
    assert any("fail" in f for f in failures)


def test_missing_p95() -> None:
    """Missing p95_us field should fail."""
    results = {
        "results": {
            "intercept": {},
            "commit": {},
            "fail": {},
        }
    }
    failures = compare(results, BASELINE, threshold=10.0)
    assert len(failures) == 3


def test_zero_baseline() -> None:
    """Zero baseline p95 should fail."""
    baseline = {
        "results": {
            "intercept": {"p95_us": 0},
            "commit": {"p95_us": 1800},
            "fail": {"p95_us": 1600},
        }
    }
    results = {
        "results": {
            "intercept": {"p95_us": 100},
            "commit": {"p95_us": 1800},
            "fail": {"p95_us": 1600},
        }
    }
    failures = compare(results, baseline, threshold=10.0)
    assert len(failures) == 1
    assert "zero" in failures[0]


def test_main_pass() -> None:
    """Main function returns 0 on pass."""
    results = {
        "results": {
            "intercept": {"p95_us": 2100},
            "commit": {"p95_us": 1800},
            "fail": {"p95_us": 1600},
        }
    }
    r_path = _write_json(results)
    b_path = _write_json(BASELINE)
    try:
        code = main(
            ["--results", str(r_path), "--baseline", str(b_path), "--threshold", "10"]
        )
        assert code == 0
    finally:
        r_path.unlink()
        b_path.unlink()


def test_main_fail() -> None:
    """Main function returns 1 on regression."""
    results = {
        "results": {
            "intercept": {"p95_us": 5000},
            "commit": {"p95_us": 5000},
            "fail": {"p95_us": 5000},
        }
    }
    r_path = _write_json(results)
    b_path = _write_json(BASELINE)
    try:
        code = main(
            ["--results", str(r_path), "--baseline", str(b_path), "--threshold", "10"]
        )
        assert code == 1
    finally:
        r_path.unlink()
        b_path.unlink()


def test_main_missing_file() -> None:
    """Main function returns 2 on missing file."""
    code = main(["--results", "/nonexistent.json", "--baseline", "/nonexistent.json"])
    assert code == 2


def test_main_invalid_json() -> None:
    """Main function returns 2 on invalid JSON."""
    fd, path_str = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    r_path = Path(path_str)
    r_path.write_text("not json")
    b_path = _write_json(BASELINE)
    try:
        code = main(["--results", str(r_path), "--baseline", str(b_path)])
        assert code == 2
    finally:
        r_path.unlink()
        b_path.unlink()
