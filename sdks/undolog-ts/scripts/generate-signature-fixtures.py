#!/usr/bin/env python3
"""Deterministic cross-language signature fixture generator.

Computes canonical JSON and BLAKE3 call-signatures for a curated set of
test vectors and writes them to ``tests/fixtures/cross-language-signatures.json``.
Every SDK implementation (Rust, Python, TypeScript) can load this file to
verify that ``call_signature`` produces byte-for-byte identical output.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path bootstrap -- allow running from the repo root without pip install -e
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SDK_PATH = os.path.join(_REPO_ROOT, "sdks", "undolog-py")

if _SDK_PATH not in sys.path:
    sys.path.insert(0, _SDK_PATH)

from undolog_sdk.signature import call_signature, canonical_json  # noqa: E402 (path bootstrap precludes top-level import)


# ---------------------------------------------------------------------------
# Fixture definitions
# ---------------------------------------------------------------------------

INPUTS: list[dict[str, Any]] = [
    # 1-5: Empty and minimal
    {
        "name": "empty_args",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 0,
        "tool_name": "ping",
        "args": {},
    },
    {
        "name": "single_key",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 0,
        "tool_name": "echo",
        "args": {"msg": "hello"},
    },
    {
        "name": "two_keys_sorted",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 0,
        "tool_name": "sort_check",
        "args": {"z": 1, "a": 2},
    },
    {
        "name": "three_keys_sorted",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 1,
        "tool_name": "sort_check_3",
        "args": {"c": 3, "a": 1, "b": 2},
    },
    {
        "name": "many_keys",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 2,
        "tool_name": "many_keys",
        "args": {"z": 9, "y": 8, "x": 7, "w": 6, "v": 5},
    },
    # 6-10: Primitive types
    {
        "name": "int_arg",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 1,
        "tool_name": "int_tool",
        "args": {"value": 42},
    },
    {
        "name": "float_arg",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 2,
        "tool_name": "float_tool",
        "args": {"value": 3.14},
    },
    {
        "name": "bool_true",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 3,
        "tool_name": "bool_tool",
        "args": {"flag": True},
    },
    {
        "name": "bool_false",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 4,
        "tool_name": "bool_tool",
        "args": {"flag": False},
    },
    {
        "name": "null_arg",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 5,
        "tool_name": "null_tool",
        "args": {"value": None},
    },
    # 11-15: Lists
    {
        "name": "int_list",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 6,
        "tool_name": "list_tool",
        "args": {"items": [1, 2, 3]},
    },
    {
        "name": "mixed_list",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 7,
        "tool_name": "mixed_list",
        "args": {"v": [1, "a", True, None]},
    },
    {
        "name": "nested_list",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 8,
        "tool_name": "nested_list",
        "args": {"matrix": [[1, 2], [3, 4]]},
    },
    {
        "name": "empty_list",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 9,
        "tool_name": "empty_list",
        "args": {"v": []},
    },
    {
        "name": "list_with_dicts",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 10,
        "tool_name": "list_dict",
        "args": {"rows": [{"b": 1, "a": 2}, {"d": 3, "c": 4}]},
    },
    # 16-20: Nested dicts
    {
        "name": "nested_dict",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 11,
        "tool_name": "nested",
        "args": {"nested": {"a": 1, "z": 2}},
    },
    {
        "name": "double_nested",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 12,
        "tool_name": "nested2",
        "args": {"a": {"b": {"c": 1}}},
    },
    {
        "name": "deep_nesting",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 13,
        "tool_name": "deep",
        "args": {"l1": {"l2": {"l3": {"l4": "deep"}}}},
    },
    {
        "name": "nested_key_order",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 14,
        "tool_name": "nested_sort",
        "args": {"outer": {"z": 9, "a": 1, "m": 5}},
    },
    {
        "name": "nested_empty",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 15,
        "tool_name": "nested_empty",
        "args": {"x": {}},
    },
    # 21-25: Unicode and special characters
    {
        "name": "unicode",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 16,
        "tool_name": "unicode",
        "args": {"msg": "héllo 世界"},
    },
    {
        "name": "special_chars",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 17,
        "tool_name": "special",
        "args": {"query": "a&b=c"},
    },
    {
        "name": "json_string",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 18,
        "tool_name": "json_string",
        "args": {"payload": '{"nested": true}'},
    },
    {
        "name": "long_string",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 19,
        "tool_name": "long_str",
        "args": {"text": "a" * 500},
    },
    {
        "name": "whitespace_string",
        "session_id": "550e8400-e29b-41d4-a716-446655440000",
        "step_index": 20,
        "tool_name": "whitespace",
        "args": {"val": "  spaced  "},
    },
    # 26-30: Real-world tool patterns
    {
        "name": "search_tool",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "step_index": 0,
        "tool_name": "search",
        "args": {"query": "hello world", "max_results": 10},
    },
    {
        "name": "email_tool",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "step_index": 1,
        "tool_name": "send_email",
        "args": {"to": "alice@example.com", "subject": "Hello", "cc": []},
    },
    {
        "name": "transfer_tool",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "step_index": 2,
        "tool_name": "transfer",
        "args": {"from": "alice", "to": "bob", "amount": 100.50},
    },
    {
        "name": "create_record",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "step_index": 3,
        "tool_name": "create_record",
        "args": {"table": "users", "data": {"name": "Alice", "age": 30}},
    },
    {
        "name": "batch_operation",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "step_index": 4,
        "tool_name": "batch",
        "args": {
            "operations": [
                {"type": "create", "data": {"id": 1}},
                {"type": "delete", "data": {"id": 2}},
            ]
        },
    },
    # 31-35: Edge cases
    {
        "name": "zero_values",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 99,
        "tool_name": "zero",
        "args": {"int": 0, "float": 0.0, "str": "", "lst": [], "dct": {}},
    },
    {
        "name": "negative_zero",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 100,
        "tool_name": "neg_zero",
        "args": {"v": -0.0},
    },
    {
        "name": "small_exponential",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 104,
        "tool_name": "small_exp",
        "args": {"boundary": 1e-6, "one_under": 9.999999e-7, "one_over": 1e-7},
    },
    {
        "name": "large_exponential",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 105,
        "tool_name": "large_exp",
        "args": {"under": 1e20, "boundary": 1e21, "over": 1.5e21},
    },
    {
        "name": "denormal_float",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 106,
        "tool_name": "denormal",
        "args": {"min": 5e-324},
    },
    {
        "name": "negative_numbers",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 101,
        "tool_name": "neg",
        "args": {"neg": -42, "pos": 7, "neg_float": -3.14},
    },
    {
        "name": "numeric_string",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 102,
        "tool_name": "num_str",
        "args": {"id": "12345"},
    },
    {
        "name": "url_string",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "step_index": 103,
        "tool_name": "url_tool",
        "args": {"url": "https://example.com/path?q=1&lang=en"},
    },
    {
        "name": "mixed_types",
        "session_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "step_index": 0,
        "tool_name": "mixed",
        "args": {
            "score": 98.6,
            "name": "test",
            "active": False,
            "tags": ["x", "y"],
            "meta": {"v": 2},
        },
    },
]


def build_fixtures(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute canonical JSON and call signature for each input vector.

    Args:
        inputs: List of parameter dicts, each containing ``session_id``,
            ``step_index``, ``tool_name``, and ``args``.

    Returns:
        List of fixture dicts with computed ``expected_json`` and
        ``expected_signature`` fields added alongside the original inputs.
    """
    fixtures: list[dict[str, Any]] = []
    for item in inputs:
        session_id = str(item["session_id"])
        step_index = int(item["step_index"])
        tool_name = str(item["tool_name"])
        args = item["args"]

        expected_json = canonical_json(args)
        expected_signature = call_signature(session_id, step_index, tool_name, args)

        fixtures.append(
            {
                "name": item["name"],
                "session_id": session_id,
                "step_index": step_index,
                "tool_name": tool_name,
                "args": args,
                "expected_json": expected_json,
                "expected_signature": expected_signature,
            }
        )

    return fixtures


def main() -> None:
    """Entry point: compute fixtures and write to output file."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    output_dir = os.path.join(_REPO_ROOT, "tests", "fixtures")
    output_path = os.path.join(output_dir, "cross-language-signatures.json")

    os.makedirs(output_dir, exist_ok=True)

    fixtures = build_fixtures(INPUTS)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(fixtures, f, indent=2, ensure_ascii=False)
        f.write("\n")

    sig_count = len(fixtures)
    log.info("Wrote %d signature fixtures to %s", sig_count, output_path)


if __name__ == "__main__":
    main()
