/** Deterministic canonical JSON and BLAKE3 call-signature computation.
 *
 * This is the most critical module in the SDK. It MUST produce byte-for-byte
 * identical output to the Python SDK's ``undolog_sdk.signature`` module for the
 * same inputs: same inputs, same 64-char hex signature, regardless of language
 * or platform.
 *
 * @module
 */

import { blake3 } from "@noble/hashes/blake3.js";
import { bytesToHex } from "@noble/hashes/utils.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Escape a string for JSON with Python's ``ensure_ascii=True`` semantics.
 *
 * Non-ASCII characters (code point >= 0x80) are emitted as ``\\uXXXX`` escape
 * sequences. This matches Python ``json.dumps(..., ensure_ascii=True)`` and
 * Rust ``serde_json`` output.
 *
 * @param s - Raw string to escape.
 * @returns JSON-encoded string literal (including surrounding double quotes).
 */
function escapeJsonString(s: string): string {
  let result = '"';
  for (let i = 0; i < s.length; i++) {
    const ch = s.charAt(i);
    const code = s.charCodeAt(i);
    switch (ch) {
      case '"':
        result += '\\"';
        break;
      case "\\":
        result += "\\\\";
        break;
      case "\b":
        result += "\\b";
        break;
      case "\f":
        result += "\\f";
        break;
      case "\n":
        result += "\\n";
        break;
      case "\r":
        result += "\\r";
        break;
      case "\t":
        result += "\\t";
        break;
      default:
        if (code < 0x20) {
          result += `\\u${code.toString(16).padStart(4, "0")}`;
        } else if (code < 0x80) {
          result += ch;
        } else {
          result += `\\u${code.toString(16).padStart(4, "0")}`;
        }
    }
  }
  return `${result}"`;
}

/** Produce a deterministic, sorted-key JSON string suitable for hashing.
 *
 * ``JSON.stringify`` serialises object properties in insertion order, which
 * varies across languages. This function recursively sorts all keys so that
 * ``{"b":1,"a":2}`` and ``{"a":2,"b":1}`` produce the same canonical string.
 *
 * Non-ASCII characters are escaped (``ensure_ascii`` semantics) to match
 * Python's ``json.dumps(..., ensure_ascii=True)`` and Rust ``serde_json``.
 *
 * @param value - A JSON-compatible value (object, array, string, number,
 *   boolean, null).
 * @returns Compact JSON string with recursively sorted keys, no whitespace.
 *
 * @example
 * ```ts
 * canonicalJson({ b: 1, a: 2 }); // '{"a":2,"b":1}'
 * canonicalJson("hello");        // '"hello"'
 * ```
 */
export function canonicalJson(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "number") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    return escapeJsonString(value);
  }
  if (Array.isArray(value)) {
    const items = value.map((v) => canonicalJson(v));
    return `[${items.join(",")}]`;
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    const pairs = keys.map((k) => `${escapeJsonString(k)}:${canonicalJson(obj[k])}`);
    return `{${pairs.join(",")}}`;
  }
  throw new TypeError(`Cannot serialise value of type ${typeof value}`);
}

/** Encode a 32-bit unsigned integer as 4 bytes in little-endian order.
 *
 * @param n - Integer value (will be truncated to 32 bits).
 * @returns A 4-byte Uint8Array in little-endian byte order.
 */
function le32(n: number): Uint8Array {
  const buf = new Uint8Array(4);
  buf[0] = n & 0xff;
  buf[1] = (n >> 8) & 0xff;
  buf[2] = (n >> 16) & 0xff;
  buf[3] = (n >> 24) & 0xff;
  return buf;
}

/** Parse a UUID string into its 16 raw bytes (network byte order).
 *
 * This matches Python's ``uuid.UUID(s).bytes`` representation: the hex groups
 * are concatenated and decoded in big-endian byte order.
 *
 * @param uuid - UUID string in standard ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``
 *   format.
 * @returns 16-byte Uint8Array.
 * @throws {TypeError} If the UUID string is not valid.
 */
function parseUuidBytes(uuid: string): Uint8Array {
  if (!UUID_RE.test(uuid)) {
    throw new TypeError(`Invalid UUID string: ${uuid}`);
  }
  const hex = uuid.replace(/-/g, "");
  const bytes = new Uint8Array(16);
  for (let i = 0; i < 16; i++) {
    bytes[i] = Number.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

/** Compute the canonical BLAKE3 call signature for a tool call.
 *
 * Every SDK (Rust, Python, TypeScript, C#) MUST produce the same 64-character
 * lowercase hex output for the same inputs. The length-prefixed encoding
 * prevents boundary attacks where two different (name, args) pairs could
 * produce the same byte sequence without delimiters.
 *
 * The BLAKE3 hash is computed over the following byte stream:
 *
 * - ``[session_id: 16 bytes]``
 * - ``[step_index: 4 bytes LE]``
 * - ``[tool_name_len: 4 bytes LE][tool_name: N bytes UTF-8]``
 * - ``[canonical_args_len: 4 bytes LE][canonical_args: M bytes UTF-8]``
 *
 * @param sessionId - UUID string identifying the session.
 * @param stepIndex - Monotonically increasing step counter within the session.
 * @param toolName - Logical name of the tool being called.
 * @param args - JSON-compatible object (dict, list, etc.) representing the
 *   tool arguments. Will be canonicalised before hashing.
 * @returns 64-character lowercase hex string (BLAKE3-256).
 * @throws {TypeError} If ``sessionId`` is not a valid UUID.
 *
 * @example
 * ```ts
 * const sig = callSignature(
 *   "550e8400-e29b-41d4-a716-446655440000",
 *   0,
 *   "send_email",
 *   { to: "alice@example.com" },
 * );
 * console.log(sig); // 64-character hex string
 * ```
 */
export function callSignature(
  sessionId: string,
  stepIndex: number,
  toolName: string,
  args: unknown,
): string {
  const sidBytes = parseUuidBytes(sessionId);
  const canon = canonicalJson(args);
  const nameBytes = new TextEncoder().encode(toolName);
  const argsBytes = new TextEncoder().encode(canon);

  const hasher = blake3.create();

  // 1. session_id as 16 raw bytes, network byte order (fixed width, no prefix)
  hasher.update(sidBytes);

  // 2. step_index as 4-byte little-endian
  hasher.update(le32(stepIndex >>> 0));

  // 3. length-prefixed tool_name
  hasher.update(le32(nameBytes.length));
  hasher.update(nameBytes);

  // 4. length-prefixed canonical args JSON
  hasher.update(le32(argsBytes.length));
  hasher.update(argsBytes);

  return bytesToHex(hasher.digest());
}
