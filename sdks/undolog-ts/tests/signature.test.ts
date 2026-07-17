import { describe, it, expect } from "vitest";
import { canonicalJson, callSignature } from "../src/signature.js";

// ── Canonical JSON ─────────────────────────────────────────────────────────

describe("canonicalJson", () => {
  it("serialises empty dict", () => {
    expect(canonicalJson({})).toBe("{}");
  });

  it("serialises empty list", () => {
    expect(canonicalJson([])).toBe("[]");
  });

  it("sorts top-level keys", () => {
    const a = canonicalJson({ z: 1, a: 2, m: 3 });
    const b = canonicalJson({ a: 2, m: 3, z: 1 });
    expect(a).toBe(b);
    expect(a).toBe('{"a":2,"m":3,"z":1}');
  });

  it("sorts nested keys", () => {
    const result = canonicalJson({ x: { z: 1, a: 2 } });
    expect(result.indexOf('"a"')).toBeLessThan(result.indexOf('"z"'));
  });

  it("preserves list order", () => {
    expect(canonicalJson([3, 1, 2])).toBe("[3,1,2]");
  });

  it("handles string values", () => {
    expect(canonicalJson("hello")).toBe('"hello"');
  });

  it("handles integer values", () => {
    expect(canonicalJson(42)).toBe("42");
  });

  it("handles float values", () => {
    expect(canonicalJson(3.14)).toBe("3.14");
  });

  it("handles boolean values", () => {
    expect(canonicalJson(true)).toBe("true");
    expect(canonicalJson(false)).toBe("false");
  });

  it("handles null value", () => {
    expect(canonicalJson(null)).toBe("null");
  });

  it("handles nested dict in list", () => {
    const v = [{ b: 1, a: 2 }, { d: 3, c: 4 }];
    expect(canonicalJson(v)).toBe('[{"a":2,"b":1},{"c":4,"d":3}]');
  });

  it("handles nested list in dict", () => {
    const v = { x: [3, 1, 2], a: [4, 5] };
    expect(canonicalJson(v)).toBe('{"a":[4,5],"x":[3,1,2]}');
  });

  it("handles deeply nested structures", () => {
    const v = { a: { b: { c: { d: [1, 2, 3] } } } };
    expect(canonicalJson(v)).toBe('{"a":{"b":{"c":{"d":[1,2,3]}}}}');
  });

  it("is deterministic", () => {
    const v = { z: 9, y: 8, x: { nested: { c: 1, a: 2 } } };
    expect(canonicalJson(v)).toBe(canonicalJson(v));
  });

  it("contains no whitespace", () => {
    const result = canonicalJson({ a: 1, b: [2, 3] });
    expect(result).not.toContain(" ");
    expect(result).not.toContain("\n");
  });

  it("handles mixed types in dict", () => {
    const v = {
      null_val: null,
      bool_val: true,
      int_val: 10,
      str_val: "text",
    };
    const result = canonicalJson(v);
    expect(result).toContain(':"text"');
    expect(result).toContain(":true");
    expect(result).toContain(":null");
    expect(result).toContain(":10");
  });

  it("escapes special characters in strings", () => {
    const v = { msg: 'hello "world" \n newline' };
    const result = canonicalJson(v);
    expect(result).toContain('"hello \\"world\\" \\n newline"');
  });

  it("handles unicode chars with ensure_ascii", () => {
    const v = { msg: "héllo 世界" };
    const result = canonicalJson(v);
    expect(result).toBe('{"msg":"h\\u00e9llo \\u4e16\\u754c"}');
  });

  it("throws for unsupported types", () => {
    expect(() => canonicalJson(undefined)).toThrow(TypeError);
  });

  it("serialises zero values", () => {
    const v = { int: 0, str: "", lst: [], dct: {} };
    const result = canonicalJson(v);
    expect(result).toBe('{"dct":{},"int":0,"lst":[],"str":""}');
  });
});

// ── Call Signature ─────────────────────────────────────────────────────────

function fakeSession(): string {
  return "00000000-0000-0000-0000-000000000000";
}

describe("callSignature", () => {
  it("returns 64 hex characters", () => {
    const sig = callSignature(fakeSession(), 0, "tool", {});
    expect(sig).toHaveLength(64);
    expect(sig).toMatch(/^[0-9a-f]+$/);
  });

  it("is deterministic", () => {
    const s = fakeSession();
    const args = { amount: 100, to: "bob" };
    expect(callSignature(s, 3, "transfer_funds", args)).toBe(
      callSignature(s, 3, "transfer_funds", args),
    );
  });

  it("differs on step", () => {
    const s = fakeSession();
    expect(callSignature(s, 1, "t", {})).not.toBe(
      callSignature(s, 2, "t", {}),
    );
  });

  it("differs on args", () => {
    const s = fakeSession();
    expect(callSignature(s, 0, "t", { a: 1 })).not.toBe(
      callSignature(s, 0, "t", { a: 2 }),
    );
  });

  it("differs on session", () => {
    expect(
      callSignature("00000000-0000-0000-0000-000000000000", 0, "t", { x: 1 }),
    ).not.toBe(
      callSignature("11111111-1111-1111-1111-111111111111", 0, "t", { x: 1 }),
    );
  });

  it("differs on tool name", () => {
    const s = fakeSession();
    expect(callSignature(s, 0, "tool_a", { a: 1 })).not.toBe(
      callSignature(s, 0, "tool_b", { a: 1 }),
    );
  });

  it("key order does not affect signature", () => {
    const s = fakeSession();
    const argsA = { z: 1, a: 2, m: 3 };
    const argsB = { a: 2, m: 3, z: 1 };
    expect(callSignature(s, 1, "test", argsA)).toBe(
      callSignature(s, 1, "test", argsB),
    );
  });

  it("throws on invalid UUID", () => {
    expect(() => callSignature("not-a-uuid", 0, "t", {})).toThrow(TypeError);
  });

  it("handles empty args", () => {
    const sig = callSignature(fakeSession(), 0, "empty_test", {});
    expect(sig).toHaveLength(64);
  });

  it("handles string args", () => {
    const sig = callSignature(fakeSession(), 0, "str_tool", { name: "hello" });
    expect(sig).toHaveLength(64);
  });

  it("handles nested args", () => {
    const sig = callSignature(
      fakeSession(),
      5,
      "nested_tool",
      { outer: { inner: [1, 2, { key: "val" }] } },
    );
    expect(sig).toHaveLength(64);
  });

  it("handles all types in args", () => {
    const sig = callSignature(
      fakeSession(),
      0,
      "all_types",
      {
        string: "text",
        int: 42,
        float: 3.14,
        bool: true,
        null_val: null,
        list: [1, "two", false],
        nested: { a: 1 },
      },
    );
    expect(sig).toHaveLength(64);
  });

  it("handles large args", () => {
    const large: Record<string, number> = {};
    for (let i = 0; i < 100; i++) {
      large[`key_${i}`] = i;
    }
    const sig = callSignature(fakeSession(), 0, "large_tool", large);
    expect(sig).toHaveLength(64);
  });
});

// ── 50 Fixtures: cross-language parity test vector suite ──────────────────
// These fixtures validate the canonical JSON and signature algorithm against
// the same inputs that Python uses.  Each fixture documents what it tests.

interface Fixture {
  name: string;
  args: unknown;
  expectedJson: string;
}

const FIXTURES: Fixture[] = [
  // 1-5: Empty and simple structures
  { name: "empty_obj", args: {}, expectedJson: "{}" },
  { name: "empty_list", args: [], expectedJson: "[]" },
  { name: "single_key", args: { a: 1 }, expectedJson: '{"a":1}' },
  { name: "two_keys_sorted", args: { z: 1, a: 2 }, expectedJson: '{"a":2,"z":1}' },
  { name: "three_keys_sorted", args: { c: 3, a: 1, b: 2 }, expectedJson: '{"a":1,"b":2,"c":3}' },
  // 6-10: Primitive types
  { name: "string_value", args: { v: "hello" }, expectedJson: '{"v":"hello"}' },
  { name: "int_value", args: { v: 42 }, expectedJson: '{"v":42}' },
  { name: "float_value", args: { v: 3.14 }, expectedJson: '{"v":3.14}' },
  { name: "true_value", args: { v: true }, expectedJson: '{"v":true}' },
  { name: "null_value", args: { v: null }, expectedJson: '{"v":null}' },
  // 11-15: Lists
  { name: "int_list", args: { v: [1, 2, 3] }, expectedJson: '{"v":[1,2,3]}' },
  { name: "mixed_list", args: { v: [1, "a", true] }, expectedJson: '{"v":[1,"a",true]}' },
  { name: "nested_list", args: { v: [[1, 2], [3, 4]] }, expectedJson: '{"v":[[1,2],[3,4]]}' },
  { name: "empty_list_val", args: { v: [] }, expectedJson: '{"v":[]}' },
  { name: "list_with_dicts", args: { v: [{ b: 1, a: 2 }] }, expectedJson: '{"v":[{"a":2,"b":1}]}' },
  // 16-20: Nested dicts
  { name: "nested_dict", args: { x: { a: 1, z: 2 } }, expectedJson: '{"x":{"a":1,"z":2}}' },
  { name: "double_nested", args: { a: { b: { c: 1 } } }, expectedJson: '{"a":{"b":{"c":1}}}' },
  { name: "mixed_nesting", args: { a: { b: [1, { c: 2 }] } }, expectedJson: '{"a":{"b":[1,{"c":2}]}}' },
  { name: "deep_nesting", args: { l1: { l2: { l3: { l4: 1 } } } }, expectedJson: '{"l1":{"l2":{"l3":{"l4":1}}}}' },
  { name: "nested_key_order", args: { x: { z: 9, a: 1, m: 5 } }, expectedJson: '{"x":{"a":1,"m":5,"z":9}}' },
  // 21-25: Real-world tool patterns
  { name: "search_tool", args: { query: "hello", max_results: 10 }, expectedJson: '{"max_results":10,"query":"hello"}' },
  { name: "email_tool", args: { to: "bob@example.com", subject: "Hi", cc: [] }, expectedJson: '{"cc":[],"subject":"Hi","to":"bob@example.com"}' },
  { name: "transfer_tool", args: { from: "alice", to: "bob", amount: 100.5 }, expectedJson: '{"amount":100.5,"from":"alice","to":"bob"}' },
  { name: "create_record", args: { table: "users", data: { name: "Alice", age: 30 } }, expectedJson: '{"data":{"age":30,"name":"Alice"},"table":"users"}' },
  { name: "delete_tool", args: { confirm: true, resource: "db_prod" }, expectedJson: '{"confirm":true,"resource":"db_prod"}' },
  // 26-30: Edge cases
  { name: "unicode_chars", args: { msg: "héllo 世界" }, expectedJson: '{"msg":"h\\u00e9llo \\u4e16\\u754c"}' },
  { name: "special_chars", args: { q: "a&b=c" }, expectedJson: '{"q":"a&b=c"}' },
  { name: "numbers_various", args: { int: 0, neg: -1, big: 999999 }, expectedJson: '{"big":999999,"int":0,"neg":-1}' },
  { name: "boolean_false", args: { flag: false }, expectedJson: '{"flag":false}' },
  { name: "list_of_lists", args: { matrix: [[1, 2], [3, 4], [5, 6]] }, expectedJson: '{"matrix":[[1,2],[3,4],[5,6]]}' },
  // 31-35: Mixed type dicts
  { name: "mixed_types_1", args: { a: 1, b: "two", c: null, d: [true] }, expectedJson: '{"a":1,"b":"two","c":null,"d":[true]}' },
  { name: "mixed_types_2", args: { flag: true, count: 0, tags: ["x", "y"] }, expectedJson: '{"count":0,"flag":true,"tags":["x","y"]}' },
  { name: "mixed_types_3", args: { nested: { x: 1 }, list: [{ a: 1 }], val: "end" }, expectedJson: '{"list":[{"a":1}],"nested":{"x":1},"val":"end"}' },
  { name: "mixed_types_4", args: { score: 98.6, name: "test", active: false }, expectedJson: '{"active":false,"name":"test","score":98.6}' },
  { name: "mixed_types_5", args: { data: null, meta: { v: 2 } }, expectedJson: '{"data":null,"meta":{"v":2}}' },
  // 36-40: Empty and edge
  { name: "single_key_empty_val", args: { x: {} }, expectedJson: '{"x":{}}' },
  { name: "list_with_nested_empty", args: { x: [{}] }, expectedJson: '{"x":[{}]}' },
  { name: "dict_with_list_of_empty", args: { x: [[], []] }, expectedJson: '{"x":[[],[]]}' },
  { name: "many_keys", args: { z: 9, y: 8, x: 7, w: 6, v: 5 }, expectedJson: '{"v":5,"w":6,"x":7,"y":8,"z":9}' },
  { name: "zero_values", args: { int: 0, str: "", lst: [], dct: {} }, expectedJson: '{"dct":{},"int":0,"lst":[],"str":""}' },
  // 41-45: Long strings
  { name: "long_string", args: { text: "a".repeat(1000) }, expectedJson: `{"text":"${"a".repeat(1000)}"}` },
  { name: "json_string", args: { json_str: '{"a":1}' }, expectedJson: '{"json_str":"{\\"a\\":1}"}' },
  { name: "url_string", args: { url: "https://example.com/path?q=1" }, expectedJson: '{"url":"https://example.com/path?q=1"}' },
  { name: "numeric_string", args: { num: "12345" }, expectedJson: '{"num":"12345"}' },
  { name: "whitespace_string", args: { ws: "  spaced  " }, expectedJson: '{"ws":"  spaced  "}' },
  // 46-50: Complex real-world
  { name: "tool_with_options", args: { options: { timeout: 30, retry: true, headers: { x: "1" } } }, expectedJson: '{"options":{"headers":{"x":"1"},"retry":true,"timeout":30}}' },
  {
    name: "batch_operation",
    args: {
      operations: [
        { type: "create", data: { id: 1 } },
        { type: "delete", data: { id: 2 } },
      ],
    },
    expectedJson: '{"operations":[{"data":{"id":1},"type":"create"},{"data":{"id":2},"type":"delete"}]}',
  },
  {
    name: "filter_params",
    args: {
      filter: { where: { age: { gt: 18 } }, order: "name", limit: 10 },
    },
    expectedJson: '{"filter":{"limit":10,"order":"name","where":{"age":{"gt":18}}}}',
  },
  {
    name: "complex_config",
    args: {
      config: {
        enabled: true,
        values: [1, 2, 3],
        meta: { desc: "test", tags: ["a", "b"] },
      },
    },
    expectedJson: '{"config":{"enabled":true,"meta":{"desc":"test","tags":["a","b"]},"values":[1,2,3]}}',
  },
  {
    name: "full_example",
    args: {
      session: "abc",
      action: "run",
      params: { x: 1, y: 2 },
      debug: false,
    },
    expectedJson: '{"action":"run","debug":false,"params":{"x":1,"y":2},"session":"abc"}',
  },
];

describe("canonicalJson fixtures", () => {
  it.each(FIXTURES)("$name", ({ args, expectedJson }: Fixture) => {
    expect(canonicalJson(args)).toBe(expectedJson);
  });

  it("has at least 50 fixtures", () => {
    expect(FIXTURES.length).toBeGreaterThanOrEqual(50);
  });
});

describe("callSignature fixtures", () => {
  it.each(FIXTURES)("$name yields 64 hex chars", ({ name, args }: Fixture) => {
    const sig = callSignature(fakeSession(), 0, name, args);
    expect(sig).toHaveLength(64);
  });

  it("signature determinism across all fixtures", () => {
    const s = fakeSession();
    for (const fx of FIXTURES) {
      expect(callSignature(s, 0, fx.name, fx.args)).toBe(
        callSignature(s, 0, fx.name, fx.args),
      );
    }
  });
});

// ── Cross-language parity test vector ────────────────────────────────────
// The expected signature below was computed by the Python SDK. The TypeScript
// SDK must produce the same 64-character hex output for the identical inputs.
// If this test fails, the cross-language callSignature invariant is broken.

const CROSS_LANG_SESSION = "550e8400-e29b-41d4-a716-446655440000";
const CROSS_LANG_STEP = 1;
const CROSS_LANG_TOOL = "send_email";
const CROSS_LANG_ARGS = {
  to: "alice@example.com",
  subject: "Hello",
};
const CROSS_LANG_EXPECTED =
  "8f20ad25773b270753b417b05437f5644997cb43e70a11a9e3b4e6d9a9d32546";

describe("cross-language parity", () => {
  it("matches Python SDK output for known inputs", () => {
    const sig = callSignature(
      CROSS_LANG_SESSION,
      CROSS_LANG_STEP,
      CROSS_LANG_TOOL,
      CROSS_LANG_ARGS,
    );
    expect(sig).toBe(CROSS_LANG_EXPECTED);
  });
});

// ── Fixed signature length tests ──────────────────────────────────────────

interface FixedFixture {
  session: string;
  step: number;
  tool: string;
  args: unknown;
  expectedLength: number;
}

const FIXED_FIXTURES: FixedFixture[] = [
  {
    session: "00000000-0000-0000-0000-000000000000",
    step: 0,
    tool: "ping",
    args: {},
    expectedLength: 64,
  },
  {
    session: "00000000-0000-0000-0000-000000000000",
    step: 1,
    tool: "echo",
    args: { msg: "hello" },
    expectedLength: 64,
  },
  {
    session: "11111111-1111-1111-1111-111111111111",
    step: 5,
    tool: "transfer",
    args: { to: "bob", amount: 100 },
    expectedLength: 64,
  },
];

describe("fixed signatures", () => {
  it.each(FIXED_FIXTURES)(
    "$tool has expected length",
    ({ session, step, tool, args, expectedLength }: FixedFixture) => {
      const sig = callSignature(session, step, tool, args);
      expect(sig).toHaveLength(expectedLength);
    },
  );
});
