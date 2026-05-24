"""Tests for canonical JSON and BLAKE3 call-signature computation.

Verifies:
    - Determinism (same inputs → same output)
    - 64-character hex output shape
    - Key-order independence
    - Recursive nesting stability
    - Edge cases (empty, deeply nested, mixed types)
    - Cross-language parity contract (50+ named fixtures)
"""

from __future__ import annotations

import uuid

import pytest

from undolog_sdk.signature import call_signature, canonical_json


# ── Canonical JSON ─────────────────────────────────────────────────────────


class TestCanonicalJson:
    def test_empty_dict(self) -> None:
        assert canonical_json({}) == "{}"

    def test_empty_list(self) -> None:
        assert canonical_json([]) == "[]"

    def test_sorts_top_level_keys(self) -> None:
        a = canonical_json({"z": 1, "a": 2, "m": 3})
        b = canonical_json({"a": 2, "m": 3, "z": 1})
        assert a == b
        assert a == '{"a":2,"m":3,"z":1}'

    def test_sorts_nested_keys(self) -> None:
        v = {"x": {"z": 1, "a": 2}}
        result = canonical_json(v)
        a_pos = result.index('"a"')
        z_pos = result.index('"z"')
        assert a_pos < z_pos

    def test_preserves_list_order(self) -> None:
        result = canonical_json([3, 1, 2])
        assert result == "[3,1,2]"

    def test_string_values(self) -> None:
        assert canonical_json("hello") == '"hello"'

    def test_integer_values(self) -> None:
        assert canonical_json(42) == "42"

    def test_float_values(self) -> None:
        assert canonical_json(3.14) == "3.14"

    def test_boolean_values(self) -> None:
        assert canonical_json(True) == "true"
        assert canonical_json(False) == "false"

    def test_null_value(self) -> None:
        assert canonical_json(None) == "null"

    def test_nested_dict_in_list(self) -> None:
        v = [{"b": 1, "a": 2}, {"d": 3, "c": 4}]
        assert canonical_json(v) == '[{"a":2,"b":1},{"c":4,"d":3}]'

    def test_nested_list_in_dict(self) -> None:
        v = {"x": [3, 1, 2], "a": [4, 5]}
        assert canonical_json(v) == '{"a":[4,5],"x":[3,1,2]}'

    def test_deeply_nested(self) -> None:
        v = {"a": {"b": {"c": {"d": [1, 2, 3]}}}}
        result = canonical_json(v)
        assert result == '{"a":{"b":{"c":{"d":[1,2,3]}}}}'

    def test_deterministic(self) -> None:
        v = {"z": 9, "y": 8, "x": {"nested": {"c": 1, "a": 2}}}
        assert canonical_json(v) == canonical_json(v)

    def test_no_whitespace(self) -> None:
        result = canonical_json({"a": 1, "b": [2, 3]})
        assert " " not in result
        assert "\n" not in result

    def test_mixed_types_in_dict(self) -> None:
        v = {
            "null_val": None,
            "bool_val": True,
            "int_val": 10,
            "str_val": "text",
        }
        result = canonical_json(v)
        assert ':"text"' in result
        assert ":true" in result
        assert ":null" in result
        assert ":10" in result

    def test_special_chars_in_strings(self) -> None:
        v = {"msg": 'hello "world" \n newline'}
        result = canonical_json(v)
        assert '"hello \\"world\\" \\n newline"' in result


# ── Call Signature ────────────────────────────────────────────────────────


def _sid() -> str:
    """Return a fresh random UUID string for use as a session_id."""
    return str(uuid.uuid4())


class TestCallSignature:
    def test_is_64_hex_chars(self) -> None:
        sig = call_signature(_sid(), 0, "tool", {})
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_is_deterministic(self) -> None:
        s = _sid()
        args = {"amount": 100, "to": "bob"}
        assert call_signature(s, 3, "transfer_funds", args) == call_signature(
            s, 3, "transfer_funds", args
        )

    def test_differs_on_step(self) -> None:
        s = _sid()
        args = {}
        assert call_signature(s, 1, "t", args) != call_signature(s, 2, "t", args)

    def test_differs_on_args(self) -> None:
        s = _sid()
        assert call_signature(s, 0, "t", {"a": 1}) != call_signature(
            s, 0, "t", {"a": 2}
        )

    def test_differs_on_session(self) -> None:
        args = {"x": 1}
        assert call_signature(_sid(), 0, "t", args) != call_signature(
            _sid(), 0, "t", args
        )

    def test_differs_on_tool_name(self) -> None:
        s = _sid()
        args = {"a": 1}
        assert call_signature(s, 0, "tool_a", args) != call_signature(
            s, 0, "tool_b", args
        )

    def test_key_order_does_not_affect_signature(self) -> None:
        s = _sid()
        args_a = {"z": 1, "a": 2, "m": 3}
        args_b = {"a": 2, "m": 3, "z": 1}
        assert call_signature(s, 1, "test", args_a) == call_signature(
            s, 1, "test", args_b
        )

    def test_invalid_uuid_raises(self) -> None:
        with pytest.raises(ValueError):
            call_signature("not-a-uuid", 0, "t", {})

    def test_empty_args(self) -> None:
        sig = call_signature(_sid(), 0, "empty_test", {})
        assert len(sig) == 64

    def test_string_args(self) -> None:
        sig = call_signature(_sid(), 0, "str_tool", {"name": "hello"})
        assert len(sig) == 64

    def test_nested_args(self) -> None:
        sig = call_signature(
            _sid(),
            5,
            "nested_tool",
            {"outer": {"inner": [1, 2, {"key": "val"}]}},
        )
        assert len(sig) == 64

    def test_all_types_in_args(self) -> None:
        sig = call_signature(
            _sid(),
            0,
            "all_types",
            {
                "string": "text",
                "int": 42,
                "float": 3.14,
                "bool": True,
                "null_val": None,
                "list": [1, "two", False],
                "nested": {"a": 1},
            },
        )
        assert len(sig) == 64

    def test_large_args(self) -> None:
        large = {f"key_{i}": i for i in range(100)}
        sig = call_signature(_sid(), 0, "large_tool", large)
        assert len(sig) == 64


# ── 50 Fixtures: cross-language parity test vector suite ──────────────────
# These fixtures validate the canonical JSON and signature algorithm against
# the same inputs that Rust uses.  Each fixture documents what it tests.
# When Rust fixture files become available, load them here instead.

FIXTURES: list[dict] = [
    # 1-5: Empty and simple structures
    {"name": "empty_obj", "args": {}, "expected_json": "{}"},
    {"name": "empty_list", "args": [], "expected_json": "[]"},
    {"name": "single_key", "args": {"a": 1}, "expected_json": '{"a":1}'},
    {
        "name": "two_keys_sorted",
        "args": {"z": 1, "a": 2},
        "expected_json": '{"a":2,"z":1}',
    },
    {
        "name": "three_keys_sorted",
        "args": {"c": 3, "a": 1, "b": 2},
        "expected_json": '{"a":1,"b":2,"c":3}',
    },
    # 6-10: Primitive types
    {"name": "string_value", "args": {"v": "hello"}, "expected_json": '{"v":"hello"}'},
    {"name": "int_value", "args": {"v": 42}, "expected_json": '{"v":42}'},
    {"name": "float_value", "args": {"v": 3.14}, "expected_json": '{"v":3.14}'},
    {"name": "true_value", "args": {"v": True}, "expected_json": '{"v":true}'},
    {"name": "null_value", "args": {"v": None}, "expected_json": '{"v":null}'},
    # 11-15: Lists
    {"name": "int_list", "args": {"v": [1, 2, 3]}, "expected_json": '{"v":[1,2,3]}'},
    {
        "name": "mixed_list",
        "args": {"v": [1, "a", True]},
        "expected_json": '{"v":[1,"a",true]}',
    },
    {
        "name": "nested_list",
        "args": {"v": [[1, 2], [3, 4]]},
        "expected_json": '{"v":[[1,2],[3,4]]}',
    },
    {"name": "empty_list_val", "args": {"v": []}, "expected_json": '{"v":[]}'},
    {
        "name": "list_with_dicts",
        "args": {"v": [{"b": 1, "a": 2}]},
        "expected_json": '{"v":[{"a":2,"b":1}]}',
    },
    # 16-20: Nested dicts
    {
        "name": "nested_dict",
        "args": {"x": {"a": 1, "z": 2}},
        "expected_json": '{"x":{"a":1,"z":2}}',
    },
    {
        "name": "double_nested",
        "args": {"a": {"b": {"c": 1}}},
        "expected_json": '{"a":{"b":{"c":1}}}',
    },
    {
        "name": "mixed_nesting",
        "args": {"a": {"b": [1, {"c": 2}]}},
        "expected_json": '{"a":{"b":[1,{"c":2}]}}',
    },
    {
        "name": "deep_nesting",
        "args": {"l1": {"l2": {"l3": {"l4": 1}}}},
        "expected_json": '{"l1":{"l2":{"l3":{"l4":1}}}}',
    },
    {
        "name": "nested_key_order",
        "args": {"x": {"z": 9, "a": 1, "m": 5}},
        "expected_json": '{"x":{"a":1,"m":5,"z":9}}',
    },
    # 21-25: Real-world tool patterns
    {
        "name": "search_tool",
        "args": {"query": "hello", "max_results": 10},
        "expected_json": '{"max_results":10,"query":"hello"}',
    },
    {
        "name": "email_tool",
        "args": {"to": "bob@example.com", "subject": "Hi", "cc": []},
        "expected_json": '{"cc":[],"subject":"Hi","to":"bob@example.com"}',
    },
    {
        "name": "transfer_tool",
        "args": {"from": "alice", "to": "bob", "amount": 100.50},
        "expected_json": '{"amount":100.5,"from":"alice","to":"bob"}',
    },
    {
        "name": "create_record",
        "args": {"table": "users", "data": {"name": "Alice", "age": 30}},
        "expected_json": '{"data":{"age":30,"name":"Alice"},"table":"users"}',
    },
    {
        "name": "delete_tool",
        "args": {"confirm": True, "resource": "db_prod"},
        "expected_json": '{"confirm":true,"resource":"db_prod"}',
    },
    # 26-30: Edge cases
    {
        "name": "unicode_chars",
        "args": {"msg": "héllo 世界"},
        "expected_json": '{"msg":"h\\u00e9llo \\u4e16\\u754c"}',
    },
    {"name": "special_chars", "args": {"q": "a&b=c"}, "expected_json": '{"q":"a&b=c"}'},
    {
        "name": "numbers_various",
        "args": {"int": 0, "neg": -1, "big": 999999},
        "expected_json": '{"big":999999,"int":0,"neg":-1}',
    },
    {
        "name": "boolean_false",
        "args": {"flag": False},
        "expected_json": '{"flag":false}',
    },
    {
        "name": "list_of_lists",
        "args": {"matrix": [[1, 2], [3, 4], [5, 6]]},
        "expected_json": '{"matrix":[[1,2],[3,4],[5,6]]}',
    },
    # 31-35: Mixed type dicts
    {
        "name": "mixed_types_1",
        "args": {"a": 1, "b": "two", "c": None, "d": [True]},
        "expected_json": '{"a":1,"b":"two","c":null,"d":[true]}',
    },
    {
        "name": "mixed_types_2",
        "args": {"flag": True, "count": 0, "tags": ["x", "y"]},
        "expected_json": '{"count":0,"flag":true,"tags":["x","y"]}',
    },
    {
        "name": "mixed_types_3",
        "args": {"nested": {"x": 1}, "list": [{"a": 1}], "val": "end"},
        "expected_json": '{"list":[{"a":1}],"nested":{"x":1},"val":"end"}',
    },
    {
        "name": "mixed_types_4",
        "args": {"score": 98.6, "name": "test", "active": False},
        "expected_json": '{"active":false,"name":"test","score":98.6}',
    },
    {
        "name": "mixed_types_5",
        "args": {"data": None, "meta": {"v": 2}},
        "expected_json": '{"data":null,"meta":{"v":2}}',
    },
    # 36-40: Empty and edge
    {"name": "single_key_empty_val", "args": {"x": {}}, "expected_json": '{"x":{}}'},
    {
        "name": "list_with_nested_empty",
        "args": {"x": [{}]},
        "expected_json": '{"x":[{}]}',
    },
    {
        "name": "dict_with_list_of_empty",
        "args": {"x": [[], []]},
        "expected_json": '{"x":[[],[]]}',
    },
    {
        "name": "many_keys",
        "args": {"z": 9, "y": 8, "x": 7, "w": 6, "v": 5},
        "expected_json": '{"v":5,"w":6,"x":7,"y":8,"z":9}',
    },
    {
        "name": "zero_values",
        "args": {"int": 0, "float": 0.0, "str": "", "lst": [], "dct": {}},
        "expected_json": '{"dct":{},"float":0.0,"int":0,"lst":[],"str":""}',
    },
    # 41-45: Long strings
    {
        "name": "long_string",
        "args": {"text": "a" * 1000},
        "expected_json": '{"text":"' + "a" * 1000 + '"}',
    },
    {
        "name": "json_string",
        "args": {"json_str": '{"a":1}'},
        "expected_json": '{"json_str":"{\\"a\\":1}"}',
    },
    {
        "name": "url_string",
        "args": {"url": "https://example.com/path?q=1"},
        "expected_json": '{"url":"https://example.com/path?q=1"}',
    },
    {
        "name": "numeric_string",
        "args": {"num": "12345"},
        "expected_json": '{"num":"12345"}',
    },
    {
        "name": "whitespace_string",
        "args": {"ws": "  spaced  "},
        "expected_json": '{"ws":"  spaced  "}',
    },
    # 46-50: Complex real-world
    {
        "name": "tool_with_options",
        "args": {"options": {"timeout": 30, "retry": True, "headers": {"x": "1"}}},
        "expected_json": '{"options":{"headers":{"x":"1"},"retry":true,"timeout":30}}',
    },
    {
        "name": "batch_operation",
        "args": {
            "operations": [
                {"type": "create", "data": {"id": 1}},
                {"type": "delete", "data": {"id": 2}},
            ]
        },
        "expected_json": '{"operations":[{"data":{"id":1},"type":"create"},{"data":{"id":2},"type":"delete"}]}',
    },
    {
        "name": "filter_params",
        "args": {
            "filter": {"where": {"age": {"gt": 18}}, "order": "name", "limit": 10}
        },
        "expected_json": '{"filter":{"limit":10,"order":"name","where":{"age":{"gt":18}}}}',
    },
    {
        "name": "complex_config",
        "args": {
            "config": {
                "enabled": True,
                "values": [1, 2, 3],
                "meta": {"desc": "test", "tags": ["a", "b"]},
            }
        },
        "expected_json": '{"config":{"enabled":true,"meta":{"desc":"test","tags":["a","b"]},"values":[1,2,3]}}',
    },
    {
        "name": "full_example",
        "args": {
            "session": "abc",
            "action": "run",
            "params": {"x": 1, "y": 2},
            "debug": False,
        },
        "expected_json": '{"action":"run","debug":false,"params":{"x":1,"y":2},"session":"abc"}',
    },
]


class TestCanonicalJsonFixtures:
    @pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["name"])
    def test_canonical_json_fixture(self, fixture: dict) -> None:
        result = canonical_json(fixture["args"])
        assert result == fixture["expected_json"]

    def test_all_fixtures_are_covered(self) -> None:
        assert len(FIXTURES) >= 50, (
            f"Expected at least 50 fixtures, got {len(FIXTURES)}"
        )


class TestCallSignatureFixtures:
    @pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["name"])
    def test_signature_is_64_hex(self, fixture: dict) -> None:
        sig = call_signature(_sid(), 0, fixture["name"], fixture["args"])
        assert len(sig) == 64

    def test_signature_determinism_across_all_fixtures(self) -> None:
        s = _sid()
        for fx in FIXTURES:
            assert call_signature(s, 0, fx["name"], fx["args"]) == call_signature(
                s, 0, fx["name"], fx["args"]
            )


# ── Fixture-driven signature validation ───────────────────────────────────
# The following tests produce known signatures for a fixed session UUID.
# These can be verified against Rust output when cross-language fixture files
# are available.

FIXED_FIXTURES = [
    {
        "session": "00000000-0000-0000-0000-000000000000",
        "step": 0,
        "tool": "ping",
        "args": {},
        "expected_length": 64,
    },
    {
        "session": "00000000-0000-0000-0000-000000000000",
        "step": 1,
        "tool": "echo",
        "args": {"msg": "hello"},
        "expected_length": 64,
    },
    {
        "session": "11111111-1111-1111-1111-111111111111",
        "step": 5,
        "tool": "transfer",
        "args": {"to": "bob", "amount": 100},
        "expected_length": 64,
    },
]


class TestFixedSignatures:
    @pytest.mark.parametrize("fx", FIXED_FIXTURES, ids=lambda f: f["tool"])
    def test_known_signature_length(self, fx: dict) -> None:
        sig = call_signature(fx["session"], fx["step"], fx["tool"], fx["args"])
        assert len(sig) == fx["expected_length"]
