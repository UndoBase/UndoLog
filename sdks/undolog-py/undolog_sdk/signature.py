"""Deterministic canonical JSON and BLAKE3 call-signature computation.

This module is the most important part of the SDK. It MUST produce byte-for-byte
identical output to the Rust crate ``undolog-types`` for the ``canonical_json``
and ``CallSignature::compute`` functions.

Cross-language invariant:
    Same inputs → same 64-char hex signature, regardless of language or platform.
"""

from __future__ import annotations

import json
import struct
import uuid
from typing import Any


def canonical_json(value: Any) -> str:
    """Produce a deterministic, sorted-key JSON string suitable for hashing.

    serde_json serialises mappings in insertion order which varies across
    languages. This function recursively sorts all keys so that
    ``{"b":1,"a":2}`` and ``{"a":2,"b":1}`` produce the same canonical string.

    Args:
        value: A JSON-compatible Python object (dict, list, str, int, float,
            bool, None).

    Returns:
        Compact JSON string with recursively sorted keys, no whitespace,
        matching Rust ``serde_json`` output for leaf values.
    """
    if isinstance(value, dict):
        pairs = [(k, canonical_json(v)) for k, v in value.items()]
        pairs.sort(key=lambda x: x[0])
        inner = ",".join(f'"{k}":{v}' for k, v in pairs)
        return f"{{{inner}}}"
    if isinstance(value, (list, tuple)):
        inner = ",".join(canonical_json(v) for v in value)
        return f"[{inner}]"
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def call_signature(
    session_id: str,
    step_index: int,
    tool_name: str,
    args: Any,
) -> str:
    """Compute the canonical call signature for a tool call.

    Every SDK (Rust, Python, TypeScript, C#) MUST produce the same 64-character
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
