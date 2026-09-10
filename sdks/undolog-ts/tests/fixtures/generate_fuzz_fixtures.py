#!/usr/bin/env python3
"""Generate canonical JSON fuzz test fixtures from the Python SDK.

Produces random JSON structures and edge cases, then records the expected
canonical JSON and BLAKE3 call signature output from the Python reference
implementation. TypeScript (and Rust, Go) tests load these fixtures and
assert byte-identical output.

Usage:
    python tests/fixtures/generate_fuzz_fixtures.py > tests/fixtures/canonical-json-fuzz.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import string
import sys
from typing import Any

from undolog_sdk.signature import call_signature, canonical_json

log = logging.getLogger(__name__)

SESSION_ID = "00000000-0000-0000-0000-000000000000"
STEP_INDEX = 0

# Deterministic seed for reproducibility in CI.
random.seed(42)


def _random_string(max_len: int = 50) -> str:
    """Generate a random string with diverse character types.

    Includes control characters, BMP non-ASCII, supplementary characters,
    JSON-escape characters, and ASCII alphanumeric. Replaces the string
    with ``"safe_string"`` if a surrogate somehow leaks through.

    Args:
        max_len: Maximum length of the generated string.

    Returns:
        A random string of up to ``max_len`` characters.
    """
    length = random.randint(0, max_len)
    chars = []
    for _ in range(length):
        r = random.random()
        if r < 0.1:
            chars.append(chr(random.randint(0, 0x1F)))
        elif r < 0.2:
            chars.append(chr(random.randint(0x80, 0xD7FF)))
        elif r < 0.25:
            chars.append(chr(random.randint(0xE000, 0xFFFF)))
        elif r < 0.30:
            chars.append(chr(random.randint(0x10000, 0x10FFFF)))
        elif r < 0.5:
            chars.append(random.choice('\\"\n\r\t'))
        else:
            chars.append(random.choice(string.ascii_letters + string.digits))
    result = "".join(chars)
    # Verify no surrogates leaked through.
    for ch in result:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            return "safe_string"
    return result


def _random_value(max_depth: int = 5) -> Any:
    """Generate a random JSON-compatible value.

    Produces booleans, None, integers (within safe range), floats, strings,
    arrays, and objects with random nesting up to ``max_depth`` levels.

    Args:
        max_depth: Maximum recursion depth for nested structures.

    Returns:
        A random JSON-compatible value.
    """
    MAX_SAFE = 9007199254740991
    if max_depth <= 0:
        return random.choice([True, False, None, 0, 1, -1, "", _random_string(10)])
    r = random.random()
    if r < 0.25:
        return random.choice([True, False, None])
    if r < 0.40:
        return random.randint(-MAX_SAFE, MAX_SAFE)
    if r < 0.55:
        return round(random.uniform(-1000.0, 1000.0), random.randint(0, 10))
    if r < 0.70:
        return _random_string(20)
    if r < 0.80:
        return [_random_value(max_depth - 1) for _ in range(random.randint(0, 5))]
    if r < 0.95:
        n_keys = random.randint(0, 6)
        return {_random_string(10): _random_value(max_depth - 1) for _ in range(n_keys)}
    # Edge: values at safe integer boundaries.
    return random.choice(
        [
            MAX_SAFE,
            -MAX_SAFE,
            MAX_SAFE - 1,
            -(MAX_SAFE - 1),
        ]
    )


def _make_fixtures() -> list[dict[str, Any]]:
    """Build the full list of fuzz test fixtures.

    Combines deterministic edge cases (empty objects, unicode, float
    boundaries, deep nesting) with 10,000 random structures.

    Returns:
        List of fixture dicts with ``name``, ``args``, ``expected_json``,
        and ``expected_signature`` keys.
    """
    fixtures: list[dict[str, Any]] = []

    # --- Edge cases (deterministic) ---
    edge_cases: list[tuple[str, Any]] = [
        ("empty_obj", {}),
        ("empty_list", []),
        ("nested_empty", {"a": {}, "b": []}),
        ("deeply_nested_50", _nested_obj(50)),
        ("unicode_bmp", {"v": "héllo"}),
        ("unicode_supplementary", {"v": "\U0001f600"}),
        ("unicode_null_char", {"v": "\u0000"}),
        ("unicode_control_chars", {"v": "\x01\x02\x1f"}),
        ("unicode_backslash", {"v": "a\\b"}),
        ("unicode_quote", {"v": 'a"b'}),
        ("unicode_mixed_scripts", {"v": "cafe\u0301"}),
        ("float_zero", {"v": 0.0}),
        ("float_neg_zero", {"v": -0.0}),
        ("float_denormal_min", {"v": 5e-324}),
        ("float_max_safe", {"v": 9007199254740991}),
        ("float_1e21", {"v": 1e21}),
        ("float_neg_1e21", {"v": -1e21}),
        ("float_small", {"v": 1e-7}),
        ("float_tiny", {"v": 9.999999e-7}),
        ("float_large_mantissa", {"v": 1.5e21}),
        ("float_pi", {"v": math.pi}),
        ("float_negative_pi", {"v": -math.pi}),
        ("empty_string", {"v": ""}),
        ("long_string", {"v": "x" * 10000}),
        ("string_with_escapes", {"v": 'line1\nline2\ttab\\"quote'}),
        (
            "all_types",
            {
                "a": 1,
                "b": "two",
                "c": None,
                "d": True,
                "e": False,
                "f": [1, 2],
                "g": {"x": 1},
            },
        ),
        ("sorted_keys_many", {chr(122 - i): i for i in range(26)}),
        ("nested_sorted", {"c": {"f": 1, "a": 2}, "a": {"z": 3, "b": 4}}),
        ("list_of_dicts", [{"b": 2, "a": 1}, {"d": 4, "c": 3}]),
        ("dict_of_lists", {"z": [3, 1], "a": [2, 4]}),
        ("mixed_nesting", {"a": [1, {"b": [2, {"c": 3}]}]}),
    ]

    for name, args in edge_cases:
        fixtures.append(_build_fixture(name, args))

    # --- Random structures ---
    for i in range(10000):
        args = _random_value(max_depth=4)
        fixtures.append(_build_fixture(f"random_{i:05d}", args))

    return fixtures


def _nested_obj(depth: int) -> dict[str, Any]:
    """Create a deeply nested object for edge-case testing.

    Args:
        depth: Number of nesting levels.

    Returns:
        A dict with ``depth`` levels of single-key nesting, leaf value ``1``.
    """
    obj: dict[str, Any] = {}
    current = obj
    for i in range(depth):
        key = f"l{i}"
        if i == depth - 1:
            current[key] = 1
        else:
            nested: dict[str, Any] = {}
            current[key] = nested
            current = nested
    return obj


def _build_fixture(name: str, args: Any) -> dict[str, Any]:
    """Build a single fixture dict with expected canonical JSON and signature.

    Args:
        name: Fixture name (used as ``tool_name`` in signature computation).
        args: JSON-compatible arguments to canonicalize.

    Returns:
        Dict with ``name``, ``args``, ``expected_json``, and
        ``expected_signature`` keys.
    """
    canon = canonical_json(args)
    sig = call_signature(SESSION_ID, STEP_INDEX, name, args)
    return {
        "name": name,
        "args": args,
        "expected_json": canon,
        "expected_signature": sig,
    }


def main() -> None:
    """Entry point for the fixture generator CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path. Defaults to stdout.",
    )
    args = parser.parse_args()
    fixtures = _make_fixtures()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(fixtures, f, ensure_ascii=False)
            f.write("\n")
        log.info("Wrote %d fixtures to %s", len(fixtures), args.output)
    else:
        json.dump(fixtures, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
