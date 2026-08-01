"""Deterministic canonical JSON and BLAKE3 call-signature computation.

This module is the most important part of the SDK. It MUST produce byte-for-byte
identical output to the Rust crate ``undolog-types`` for the ``canonical_json``
and ``CallSignature::compute`` functions.

Cross-language invariant:
    Same inputs → same 64-char hex signature, regardless of language or platform.
"""

from __future__ import annotations

import json
import math
import struct
import uuid
from typing import Any

_ES6_EXP_THRESHOLD = 21


def _es6_float(value: float) -> str:
    """Serialise a float exactly as ECMAScript ``JSON.stringify`` does.

    Python's ``json.dumps`` and ``repr`` use different formatting rules than
    ECMAScript (e.g. ``1e-06`` vs ``0.000001``, ``-0.0`` vs ``0``). This
    function reproduces the ECMAScript Number::toString output so the canonical
    JSON bytes match the TypeScript SDK byte-for-byte.

    Args:
        value: A finite float.

    Returns:
        The ECMAScript-compatible serialisation of the float.

    Raises:
        ValueError: If ``value`` is not finite (``nan``, ``inf``, ``-inf``).
    """
    if not math.isfinite(value):
        raise ValueError(f"Invalid JSON number: {value!r}")
    if value == 0:
        return "0"
    text = repr(value)
    sign = "-" if text.startswith("-") else ""
    if sign:
        text = text[1:]
    exp_str = ""
    exp_val = 0
    q = text.find("e")
    if q > 0:
        exp_str = text[q:]
        if exp_str[2:3] == "0":
            exp_str = exp_str[:2] + exp_str[3:]
        text = text[:q]
        exp_val = int(exp_str[1:])
    first, dot, last = text.partition(".")
    if last == "0":
        dot = ""
        last = ""
    if 0 < exp_val < _ES6_EXP_THRESHOLD:
        first += last
        last = ""
        dot = ""
        exp_str = ""
        pad = exp_val + 1 - len(first)
        if pad > 0:
            first += "0" * pad
    elif -7 < exp_val < 0:
        last = first + last
        first = "0"
        dot = "."
        exp_str = ""
        pad = -exp_val - 1
        if pad > 0:
            last = "0" * pad + last
    return f"{sign}{first}{dot}{last}{exp_str}"


def canonical_json(value: Any) -> str:
    """Produce a deterministic, sorted-key JSON string suitable for hashing.

    serde_json serialises mappings in insertion order which varies across
    languages. This function recursively sorts all keys so that
    ``{"b":1,"a":2}`` and ``{"a":2,"b":1}`` produce the same canonical string.

    Numbers follow the ECMAScript ``JSON.stringify`` rules (RFC 8785):
    ``0.0`` and ``-0.0`` both serialise as ``0``, small and large values use
    exponential notation matching JavaScript, and exponents have no leading
    zeros. This keeps the canonical bytes identical to the TypeScript and Rust
    SDKs.

    Args:
        value: A JSON-compatible Python object (dict, list, str, int, float,
            bool, None).

    Returns:
        Compact JSON string with recursively sorted keys, no whitespace,
        matching the TypeScript and Rust SDK output for leaf values.

    Raises:
        ValueError: If ``value`` contains a non-finite float (``nan``,
            ``inf``, ``-inf``). These are rejected to match the TypeScript
            SDK, which throws a ``TypeError`` for the same inputs.
    """
    if isinstance(value, dict):
        pairs = [(k, canonical_json(v)) for k, v in value.items()]
        pairs.sort(key=lambda x: x[0])
        inner = ",".join(f'"{k}":{v}' for k, v in pairs)
        return f"{{{inner}}}"
    if isinstance(value, (list, tuple)):
        inner = ",".join(canonical_json(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _es6_float(value)
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "null"
    return _escape_json_string(value)


def _escape_json_string(value: str) -> str:
    """Encode a string as JSON with ``ensure_ascii`` semantics.

    Matches Python ``json.dumps(value, ensure_ascii=True)`` output.

    Args:
        value: String to encode.

    Returns:
        The JSON-encoded string literal (including surrounding double quotes).
    """
    return json.dumps(value, ensure_ascii=True)


def call_signature(
    session_id: str,
    step_index: int,
    tool_name: str,
    args: Any,
) -> str:
    """Compute the canonical call signature for a tool call.

    Every SDK (Rust, Python, TypeScript) MUST produce the same 64-character
    lowercase hex output for the same inputs. The length-prefixed encoding
    prevents boundary attacks where two different (name, args) pairs could
    produce the same byte sequence without delimiters.

    The BLAKE3 hash is computed over the following byte stream:

        [session_id: 16 bytes]
        [step_index: 4 bytes LE]
        [tool_name_len: 4 bytes LE][tool_name: N bytes UTF-8]
        [canonical_args_len: 4 bytes LE][canonical_args: M bytes UTF-8]

    Args:
        session_id: UUID string identifying the session.
        step_index: Monotonically increasing step counter within the session.
        tool_name: Logical name of the tool being called.
        args: JSON-compatible object (dict, list, etc.) representing the
            tool arguments. Will be canonicalized before hashing.

    Returns:
        64-character lowercase hex string (BLAKE3-256).

    Raises:
        ValueError: If session_id is not a valid UUID.
    """
    try:
        sid = uuid.UUID(session_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"session_id must be a valid UUID string, got {session_id!r}"
        ) from exc

    canon = canonical_json(args)
    name_bytes = tool_name.encode("utf-8")
    args_bytes = canon.encode("utf-8")

    # blake3 is imported lazily: the C-ext wheel is not always installed in
    # every environment, and this module is the only one that needs it.
    import blake3

    hasher = blake3.blake3()

    # 1. session_id as 16 raw bytes, network byte order (fixed width → no prefix)
    hasher.update(sid.bytes)

    # 2. step_index as 4-byte little-endian
    hasher.update(struct.pack("<I", step_index))

    # 3. length-prefixed tool_name
    hasher.update(struct.pack("<I", len(name_bytes)))
    hasher.update(name_bytes)

    # 4. length-prefixed canonical args JSON
    hasher.update(struct.pack("<I", len(args_bytes)))
    hasher.update(args_bytes)

    return hasher.hexdigest()
