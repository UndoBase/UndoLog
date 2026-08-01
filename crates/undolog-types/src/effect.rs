//! Core effect types - the unit of the UndoLog effect log.

use crate::{
    ids::{ApprovalRequestId, EffectId, OrgId, SessionId, ToolId},
    tier::ToolTier,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

// ── CallSignature ─────────────────────────────────────────────────────────────

/// A 256-bit BLAKE3 hash that uniquely identifies one tool call invocation.
///
/// Input: `BLAKE3( [len:u32le][session_id_bytes] || [step:u32le] || [len:u32le][tool_name] || [len:u32le][canonical_args_json] )`
///
/// Properties:
/// - Deterministic: same inputs → same output, always.
/// - Cross-language: the Python and TypeScript SDKs implement identical logic.
/// - Stored as 64 lowercase hex characters (`char(64)` in PostgreSQL).
///
/// The `UNIQUE` constraint on `undolog_effect_log.call_signature` plus
/// `ON CONFLICT DO NOTHING` enforces exactly-once at the DB layer.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct CallSignature(pub String);

impl CallSignature {
    /// Compute the canonical call signature for a tool call.
    ///
    /// Every SDK (Rust, Python, TypeScript) MUST produce the same output
    /// for the same inputs. The length-prefixed encoding prevents boundary
    /// attacks where two different (name, args) pairs could produce the same
    /// byte sequence without delimiters.
    pub fn compute(
        session_id: &SessionId,
        step_index: u32,
        tool_name: &str,
        canonical_args: &serde_json::Value,
    ) -> Self {
        let mut hasher = blake3::Hasher::new();

        // session_id bytes (16 bytes, fixed length - no length prefix needed)
        hasher.update(session_id.as_uuid().as_bytes());

        // step_index as 4-byte little-endian
        hasher.update(&step_index.to_le_bytes());

        // length-prefixed tool_name
        let name_bytes = tool_name.as_bytes();
        hasher.update(&(name_bytes.len() as u32).to_le_bytes());
        hasher.update(name_bytes);

        // length-prefixed canonical JSON of args
        let canon = canonical_json(canonical_args);
        let args_bytes = canon.as_bytes();
        hasher.update(&(args_bytes.len() as u32).to_le_bytes());
        hasher.update(args_bytes);

        Self(hasher.finalize().to_hex().to_string())
    }

    /// Return the underlying hex string.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for CallSignature {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Produce a deterministic, sorted-key JSON string suitable for hashing.
///
/// `serde_json` serialises maps in insertion order which varies across
/// languages. This function recursively sorts all object keys so that
/// `{"b":1,"a":2}` and `{"a":2,"b":1}` produce the same canonical string.
///
/// Non-finite floats are rejected by construction: `serde_json::Number` only
/// stores finite values (`Number::from_f64` returns `None` for NaN and
/// infinity, and `Value::from` coerces such values to `Null`). This matches
/// the rejection semantics of the Python SDK (`ValueError`) and the
/// TypeScript SDK (`TypeError`), and satisfies the JSON Canonicalization
/// Scheme requirement that NaN and Infinity cause an error (RFC 8785,
/// Section 3.2.2.3).
///
/// Negative zero serialises as `0`, matching the JSON Canonicalization
/// Scheme (RFC 8785) and the ECMAScript `JSON.stringify` rules the Python and
/// TypeScript SDKs follow. Floats use ECMAScript Number::toString formatting:
/// fixed notation for `[1e-6, 1e21)`, exponential notation (no leading zeros
/// in the exponent) elsewhere.
///
/// This function is `pub` so SDK implementations in other crates can
/// cross-reference the exact algorithm.
pub fn canonical_json(v: &serde_json::Value) -> String {
    use serde_json::Value;
    match v {
        Value::Object(map) => {
            let mut pairs: Vec<(&str, String)> =
                map.iter().map(|(k, v)| (k.as_str(), canonical_json(v))).collect();
            pairs.sort_by_key(|(k, _)| *k);
            let inner =
                pairs.iter().map(|(k, v)| format!("\"{}\":{}", k, v)).collect::<Vec<_>>().join(",");
            format!("{{{}}}", inner)
        }
        Value::Array(arr) => {
            let inner = arr.iter().map(canonical_json).collect::<Vec<_>>().join(",");
            format!("[{}]", inner)
        }
        Value::Null => "null".to_owned(),
        Value::Bool(b) => b.to_string(),
        // Integral values (i64/u64) are serialised as-is. Float values go
        // through es6_format so their rendering matches ECMAScript
        // JSON.stringify (RFC 8785), which the Python and TypeScript SDKs
        // mirror. Non-finite floats cannot be stored in a Number, so this
        // cannot produce `null` or `NaN`.
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => i.to_string(),
            (None, Some(u), _) => u.to_string(),
            (None, None, Some(f)) => es6_format(f),
            (None, None, None) => n.to_string(),
        },
        // Escaping a String cannot fail; the unwrap is unreachable.
        Value::String(s) => serde_json::to_string(s).unwrap_or_default(),
    }
}

/// Serialise a float exactly as ECMAScript `JSON.stringify` does.
///
/// `serde_json::Number::to_string` uses Rust Display semantics which differ
/// from ECMAScript for negative zero (`-0.0` vs `0`) and for magnitude
/// boundaries (`1e+21` vs `1e21`, `0.000001` vs `1e-6`). This mirrors the
/// Number::toString algorithm (RFC 8785, Section 3.2.2.2): fixed notation for
/// `[1e-6, 1e21)`, exponential notation elsewhere with no leading zeros in the
/// exponent.
///
/// Ryu and Rust's `{:e}` lower-exponential formatter emit the shortest
/// round-tripping representation, so the digits match V8's output byte for
/// byte; only the zero and exponent-presentation differences remain.
fn es6_format(f: f64) -> String {
    if f == 0.0 {
        return "0".to_owned();
    }
    let abs = f.abs();
    if (1e-6..1e21).contains(&abs) {
        format!("{f}")
    } else {
        let s = format!("{f:e}");
        match s.find('e') {
            Some(i) if s.as_bytes().get(i + 1) != Some(&b'-') => {
                format!("{}e+{}", &s[..i], &s[i + 1..])
            }
            _ => s,
        }
    }
}

// ── ToolCall ──────────────────────────────────────────────────────────────────

/// An intercepted MCP tool call, as received by the Go proxy and forwarded
/// to the Rust Effect Engine via gRPC.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub org_id: OrgId,
    pub session_id: SessionId,
    /// `None` when the tool was not found in the registry (unknown tool).
    pub tool_id: Option<ToolId>,
    pub tool_name: String,
    pub tool_version: String,
    pub tier: ToolTier,
    /// Monotonically increasing per session; determines undo stack order.
    pub step_index: u32,
    /// Raw args as received from the MCP call.
    pub args: serde_json::Value,
    pub intercepted_at: DateTime<Utc>,
}

impl ToolCall {
    /// Compute the `CallSignature` for this tool call.
    pub fn signature(&self) -> CallSignature {
        CallSignature::compute(&self.session_id, self.step_index, &self.tool_name, &self.args)
    }
}

// ── ToolResult ────────────────────────────────────────────────────────────────

/// The output of a successfully executed tool call.
/// Cached in `undolog_effect_log.result_snapshot` for replay on restore.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    pub success: bool,
    pub output: serde_json::Value,
    pub error: Option<String>,
    pub duration_ms: u64,
}

// ── EffectState ───────────────────────────────────────────────────────────────

/// Lifecycle state of a single tool call effect in the log.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectState {
    /// Registered in the log; not yet executing.
    Pending,
    /// Currently executing.
    Executing,
    /// Completed successfully; result cached.
    Committed,
    /// Compensation function is running.
    Compensating,
    /// Compensation completed; step has been undone.
    Compensated,
    /// Compensation failed permanently; requires manual intervention.
    CompensationFailed,
    /// Human approved an Irreversible action; execution may proceed.
    Approved,
    /// Human rejected; session halted.
    Rejected,
    /// Result served from cache (exactly-once replay path).
    Replayed,
}

impl EffectState {
    /// Returns `true` if the effect reached a final state (no further transitions).
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            EffectState::Committed
                | EffectState::Compensated
                | EffectState::CompensationFailed
                | EffectState::Rejected
                | EffectState::Replayed
        )
    }
}

impl std::fmt::Display for EffectState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = serde_json::to_value(self)
            .ok()
            .and_then(|v| v.as_str().map(str::to_owned))
            .unwrap_or_default();
        write!(f, "{}", s)
    }
}

// ── EffectRecord ──────────────────────────────────────────────────────────────

/// A full effect record matching a row in `undolog_effect_log`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EffectRecord {
    /// Unique effect identifier.
    pub effect_id: EffectId,
    /// Tenant organisation.
    pub org_id: OrgId,
    /// Session this effect belongs to.
    pub session_id: SessionId,
    /// Registered tool identifier (None if unregistered).
    pub tool_id: Option<ToolId>,
    /// Deterministic hash of (session, step, name, args).
    pub call_signature: CallSignature,
    /// Tool name at invocation time.
    pub tool_name: String,
    /// Tool version at invocation time.
    pub tool_version: String,
    /// Runtime tier classification.
    pub tier: ToolTier,
    /// Monotonically increasing step number within the session.
    pub step_index: u32,
    /// Immutable snapshot of the original call arguments.
    pub args_snapshot: serde_json::Value,
    /// Cached tool result (None until committed).
    pub result_snapshot: Option<ToolResult>,
    /// Current lifecycle state.
    pub state: EffectState,
    /// Compensation arguments (None for Safe/Irreversible tiers).
    pub compensation_args: Option<serde_json::Value>,
    /// When execution started.
    pub executed_at: DateTime<Utc>,
    /// When the effect was committed.
    pub committed_at: Option<DateTime<Utc>>,
    /// When compensation completed.
    pub compensated_at: Option<DateTime<Utc>>,
    /// Number of times this effect was replayed (idempotent delivery).
    pub replay_count: u16,
    /// Most recent replay timestamp.
    pub last_replayed_at: Option<DateTime<Utc>>,
    /// Linked approval request for Irreversible tier.
    pub approval_request_id: Option<ApprovalRequestId>,
}

// ── InterceptOutcome ──────────────────────────────────────────────────────────

/// What the Effect Engine instructs the Go MCP proxy to do next.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum InterceptOutcome {
    /// New call; safe to execute. `effect_id` is the log entry just created.
    Execute { effect_id: EffectId },
    /// Duplicate detected (same `call_signature`). Return the cached result.
    Replay { effect_id: EffectId, result: ToolResult },
    /// Irreversible action; execution suspended pending human approval.
    AwaitingApproval { effect_id: EffectId, approval_request_id: ApprovalRequestId },
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use uuid::Uuid;

    fn sid() -> SessionId {
        SessionId::from(Uuid::new_v4())
    }

    #[test]
    fn signature_is_deterministic() {
        let s = sid();
        let args = json!({"amount": 100, "to": "bob"});
        assert_eq!(
            CallSignature::compute(&s, 3, "transfer_funds", &args),
            CallSignature::compute(&s, 3, "transfer_funds", &args),
        );
    }

    #[test]
    fn signature_length_is_64() {
        let sig = CallSignature::compute(&sid(), 0, "tool", &json!({}));
        assert_eq!(sig.as_str().len(), 64);
        assert!(sig.as_str().chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn signature_differs_on_step() {
        let s = sid();
        let args = json!({});
        assert_ne!(
            CallSignature::compute(&s, 1, "t", &args),
            CallSignature::compute(&s, 2, "t", &args),
        );
    }

    #[test]
    fn signature_differs_on_args() {
        let s = sid();
        assert_ne!(
            CallSignature::compute(&s, 0, "t", &json!({"a": 1})),
            CallSignature::compute(&s, 0, "t", &json!({"a": 2})),
        );
    }

    #[test]
    fn canonical_json_sorts_keys() {
        let a = json!({"z": 1, "a": 2, "m": 3});
        let b = json!({"a": 2, "m": 3, "z": 1});
        assert_eq!(canonical_json(&a), canonical_json(&b));
    }

    #[test]
    fn canonical_json_nested() {
        let v = json!({"x": {"z": 1, "a": 2}});
        let result = canonical_json(&v);
        let a_pos = result.find("\"a\"").unwrap();
        let z_pos = result.find("\"z\"").unwrap();
        assert!(a_pos < z_pos, "nested keys must be sorted");
    }

    #[test]
    fn canonical_json_negative_zero_matches_python_and_ts() {
        assert_eq!(canonical_json(&json!(-0.0)), "0");
        assert_eq!(canonical_json(&json!(0.0)), "0");
        assert_eq!(canonical_json(&json!({"v": -0.0})), "{\"v\":0}");
    }

    #[test]
    fn canonical_json_es6_float_boundaries() {
        // Fixed notation for [1e-6, 1e21), exponential elsewhere.
        assert_eq!(canonical_json(&json!(1e-6)), "0.000001");
        assert_eq!(canonical_json(&json!(1e-7)), "1e-7");
        assert_eq!(canonical_json(&json!(9.999999e-7)), "9.999999e-7");
        assert_eq!(canonical_json(&json!(1e-5)), "0.00001");
        assert_eq!(canonical_json(&json!(1e20)), "100000000000000000000");
        assert_eq!(canonical_json(&json!(1e21)), "1e+21");
        assert_eq!(canonical_json(&json!(1.5e21)), "1.5e+21");
        assert_eq!(canonical_json(&json!(5e-324)), "5e-324");
        assert_eq!(canonical_json(&json!(-1e-7)), "-1e-7");
        assert_eq!(canonical_json(&json!(123.456)), "123.456");
        assert_eq!(canonical_json(&json!(0.30000000000000004)), "0.30000000000000004");
    }

    #[test]
    fn canonical_json_rejects_non_finite_at_construction() {
        // serde_json cannot represent non-finite floats: Number::from_f64
        // returns None, and Value::from coerces to Null. This is the Rust
        // equivalent of Python's ValueError and TypeScript's TypeError: a
        // non-finite float can never reach canonical_json.
        assert_eq!(serde_json::Number::from_f64(f64::NAN), None);
        assert_eq!(serde_json::Number::from_f64(f64::INFINITY), None);
        assert_eq!(serde_json::Value::from(f64::NAN), serde_json::Value::Null);
        assert_eq!(json!({"v": f64::NAN}), json!({"v": null}));
    }

    #[test]
    fn effect_state_terminal() {
        for s in [
            EffectState::Committed,
            EffectState::Compensated,
            EffectState::CompensationFailed,
            EffectState::Rejected,
            EffectState::Replayed,
        ] {
            assert!(s.is_terminal(), "{s:?} should be terminal");
        }
        for s in [
            EffectState::Pending,
            EffectState::Executing,
            EffectState::Compensating,
            EffectState::Approved,
        ] {
            assert!(!s.is_terminal(), "{s:?} should not be terminal");
        }
    }
}
