#!/usr/bin/env python3
"""Generate cross-language signature parity test fixtures from the Python SDK.

Produces deterministic test vectors for verifying that TypeScript (and Rust, Go)
call_signature and canonical_json output match the Python reference.

Usage:
    python tests/fixtures/generate_cross_lang_fixtures.py -o tests/fixtures/cross-language-signatures.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from undolog_sdk.signature import call_signature, canonical_json

log = logging.getLogger(__name__)

SESSION_ID = "550e8400-e29b-41d4-a716-446655440000"


def _build_fixture(
    name: str,
    session_id: str,
    step_index: int,
    tool_name: str,
    args: Any,
) -> dict[str, Any]:
    """Build a single fixture dict with expected canonical JSON and signature.

    Args:
        name: Fixture name for test identification.
        session_id: UUID string for signature computation.
        step_index: Step index for signature computation.
        tool_name: Tool name for signature computation.
        args: JSON-compatible arguments to canonicalize.

    Returns:
        Dict with name, session_id, step_index, tool_name, args,
        expected_json, and expected_signature keys.
    """
    canon = canonical_json(args)
    sig = call_signature(session_id, step_index, tool_name, args)
    return {
        "name": name,
        "session_id": session_id,
        "step_index": step_index,
        "tool_name": tool_name,
        "args": args,
        "expected_json": canon,
        "expected_signature": sig,
    }


def _make_fixtures() -> list[dict[str, Any]]:
    """Build the full list of cross-language parity fixtures.

    Returns:
        List of fixture dicts.
    """
    fixtures: list[dict[str, Any]] = []
    idx = 0

    def add(name: str, tool: str, args: Any) -> None:
        nonlocal idx
        fixtures.append(_build_fixture(name, SESSION_ID, idx, tool, args))
        idx += 1

    # ── Basic types ──────────────────────────────────────────────────────
    add("empty_args", "ping", {})
    add("single_key", "echo", {"msg": "hello"})
    add("two_keys_sorted", "echo", {"b": 2, "a": 1})
    add("three_keys_sorted", "echo", {"c": 3, "a": 1, "b": 2})
    add("many_keys", "echo", {chr(122 - i): i for i in range(26)})

    # ── Numeric edge cases ──────────────────────────────────────────────
    add("int_arg", "calc", {"value": 42})
    add("float_arg", "calc", {"value": 3.14})
    add("negative_zero", "calc", {"value": -0.0})
    add("positive_zero", "calc", {"value": 0.0})
    add("small_exponential", "calc", {"value": 1e-7})
    add("large_exponential", "calc", {"value": 1e21})
    add("denormal_float", "calc", {"value": 5e-324})
    add("max_safe_integer", "calc", {"value": 9007199254740991})
    add("negative_numbers", "calc", {"a": -1, "b": -42, "c": -999999})

    # ── Boolean and null ────────────────────────────────────────────────
    add("bool_true", "flag", {"enabled": True})
    add("bool_false", "flag", {"enabled": False})
    add("null_arg", "flag", {"value": None})
    add("mixed_bool_null", "flag", {"a": True, "b": None, "c": False})

    # ── Lists ───────────────────────────────────────────────────────────
    add("int_list", "batch", {"ids": [1, 2, 3]})
    add("mixed_list", "batch", {"data": [1, "two", True, None]})
    add("nested_list", "batch", {"matrix": [[1, 2], [3, 4]]})
    add("empty_list", "batch", {"items": []})
    add("list_with_dicts", "batch", {"items": [{"b": 2, "a": 1}, {"d": 4, "c": 3}]})

    # ── Nested objects ──────────────────────────────────────────────────
    add("nested_dict", "struct", {"a": {"b": 1}})
    add("double_nested", "struct", {"a": {"b": {"c": 1}}})
    add("deep_nesting", "struct", {"l0": {"l1": {"l2": {"l3": {"l4": 1}}}}})
    add("nested_key_order", "struct", {"z": {"a": 1, "z": 2}, "a": {"z": 3, "a": 4}})
    add("nested_empty", "struct", {"a": {}, "b": []})

    # ── String edge cases ───────────────────────────────────────────────
    add("unicode_bmp", "text", {"v": "hello\u00e9"})
    add("unicode_supplementary", "text", {"v": "\U0001f600"})
    add("unicode_null_char", "text", {"v": "\u0000"})
    add("unicode_control_chars", "text", {"v": "\x01\x02\x1f"})
    add("unicode_backslash", "text", {"v": "a\\b"})
    add("unicode_quote", "text", {"v": 'a"b'})
    add("special_chars", "text", {"v": "line1\nline2\ttab"})
    add("empty_string", "text", {"v": ""})
    add("long_string", "text", {"v": "x" * 1000})
    add("whitespace_string", "text", {"v": "  \t\n  "})

    # ── Unicode keys ────────────────────────────────────────────────────
    add("unicode_key_bmp", "text", {"k\u00e9y": "value"})
    add("unicode_key_supplementary", "text", {"\U0001f600key": "value"})
    add("unicode_key_control", "text", {"k\u0000ey": "value"})

    # ── Mixed types ─────────────────────────────────────────────────────
    add(
        "all_types",
        "struct",
        {
            "a": 1,
            "b": "two",
            "c": None,
            "d": True,
            "e": False,
            "f": [1, 2],
            "g": {"x": 1},
        },
    )
    add("numeric_string", "text", {"v": "12345"})
    add("url_string", "text", {"v": "https://example.com/path?q=1&r=2"})

    # ── Realistic tool calls ────────────────────────────────────────────
    add("search_tool", "web_search", {"query": "rust blake3 crate", "limit": 5})
    add(
        "email_tool",
        "send_email",
        {"to": "alice@example.com", "subject": "Hello", "body": "Hi!"},
    )
    add(
        "transfer_tool",
        "transfer_funds",
        {"from": "acc_1", "to": "acc_2", "amount": 100.50},
    )
    add(
        "create_record",
        "db_create",
        {"table": "users", "data": {"name": "Bob", "age": 30}},
    )
    add(
        "batch_operation",
        "batch_execute",
        {
            "ops": [
                {"action": "create", "id": 1},
                {"action": "update", "id": 2, "patch": {"name": "Alice"}},
            ]
        },
    )

    return fixtures


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
            json.dump(fixtures, f, ensure_ascii=False, indent=2)
            f.write("\n")
        log.info("Wrote %d fixtures to %s", len(fixtures), args.output)
    else:
        json.dump(fixtures, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
