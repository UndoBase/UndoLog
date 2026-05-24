//! Property-based tests for the UndoLog Effect Engine core invariants.
//!
//! These tests use `proptest` to generate arbitrary inputs and verify that
//! the invariants hold regardless of input. This is the correct testing
//! strategy for the Effect Engine because the invariants are universal:
//! they must hold for ALL inputs, not just example cases.
//!
//! Run: `cargo test -p undolog-engine --test property_tests`

use proptest::prelude::*;
use serde_json::json;
use undolog_types::effect::{canonical_json, CallSignature};

// ─────────────────────────────────────────────────────────────
// Invariant 1: CallSignature determinism
//
// For the same inputs, the signature must ALWAYS be the same.
// This is foundational to exactly-once semantics.
// ─────────────────────────────────────────────────────────────

proptest! {
    #[test]
    fn signature_is_deterministic_for_same_inputs(
        // Generate arbitrary session UUIDs and tool names
        session_bytes in proptest::array::uniform16(0u8..),
        step in 0u32..10000,
        tool_name in "[a-z_]{3,30}",
        amount in 0i64..1_000_000,
        recipient in "[a-z]{3,20}",
    ) {
        let session_id = undolog_types::ids::SessionId::from(
            uuid::Uuid::from_bytes(session_bytes)
        );
        let args = json!({ "amount": amount, "to": recipient });

        let sig1 = CallSignature::compute(&session_id, step, &tool_name, &args);
        let sig2 = CallSignature::compute(&session_id, step, &tool_name, &args);

        prop_assert_eq!(sig1, sig2);
    }
}

// ─────────────────────────────────────────────────────────────
// Invariant 2: CallSignature uniqueness
//
// Different (session_id, step_index) pairs must produce different signatures.
// Collisions in BLAKE3 are computationally infeasible but we verify the
// property holds for generated inputs.
// ─────────────────────────────────────────────────────────────

proptest! {
    #[test]
    fn different_step_indices_produce_different_signatures(
        session_bytes in proptest::array::uniform16(0u8..),
        step_a in 0u32..5000,
        step_b in 5001u32..10000,   // guaranteed different from step_a
        tool_name in "[a-z_]{3,20}",
        amount in 0i64..100_000,
    ) {
        let session_id = undolog_types::ids::SessionId::from(
            uuid::Uuid::from_bytes(session_bytes)
        );
        let args = json!({ "amount": amount });

        let sig_a = CallSignature::compute(&session_id, step_a, &tool_name, &args);
        let sig_b = CallSignature::compute(&session_id, step_b, &tool_name, &args);

        // step_a != step_b guarantees different signatures
        prop_assert_ne!(sig_a, sig_b);
    }
}

proptest! {
    #[test]
    fn different_session_ids_produce_different_signatures(
        session_a in proptest::array::uniform16(0u8..),
        session_b in proptest::array::uniform16(0u8..),
        step in 0u32..100,
        tool_name in "[a-z]{5,10}",
    ) {
        // If the two session IDs happen to be the same, skip this test case.
        prop_assume!(session_a != session_b);

        let sid_a = undolog_types::ids::SessionId::from(uuid::Uuid::from_bytes(session_a));
        let sid_b = undolog_types::ids::SessionId::from(uuid::Uuid::from_bytes(session_b));
        let args  = json!({});

        let sig_a = CallSignature::compute(&sid_a, step, &tool_name, &args);
        let sig_b = CallSignature::compute(&sid_b, step, &tool_name, &args);

        prop_assert_ne!(sig_a, sig_b);
    }
}

// ─────────────────────────────────────────────────────────────
// Invariant 3: CallSignature output is always 64 hex chars
// ─────────────────────────────────────────────────────────────

proptest! {
    #[test]
    fn signature_is_always_64_hex_chars(
        session_bytes in proptest::array::uniform16(0u8..),
        step in 0u32..10000,
        tool_name in "[a-z_]{3,30}",
        // Generate arbitrary JSON-serialisable numbers and strings
        value in -1_000_000i64..1_000_000,
    ) {
        let session_id = undolog_types::ids::SessionId::from(
            uuid::Uuid::from_bytes(session_bytes)
        );
        let args = json!({ "v": value });
        let sig  = CallSignature::compute(&session_id, step, &tool_name, &args);

        prop_assert_eq!(sig.as_str().len(), 64);
        prop_assert!(sig.as_str().chars().all(|c| c.is_ascii_hexdigit()));
    }
}

// ─────────────────────────────────────────────────────────────
// Invariant 4: Canonical JSON - different insertion orders
// produce the same canonical string
// ─────────────────────────────────────────────────────────────

proptest! {
    #[test]
    fn canonical_json_is_order_independent(
        key_a in "[a-m]{3,8}",
        key_b in "[n-z]{3,8}",
        val_a in 0i64..10000,
        val_b in 0i64..10000,
    ) {
        prop_assume!(key_a != key_b);

        // Same data, different insertion order
        let obj_1 = serde_json::json!({ key_a.clone(): val_a, key_b.clone(): val_b });
        let obj_2 = serde_json::json!({ key_b.clone(): val_b, key_a.clone(): val_a });

        prop_assert_eq!(canonical_json(&obj_1), canonical_json(&obj_2));
    }
}

proptest! {
    #[test]
    fn canonical_json_same_inputs_same_output(
        key in "[a-z]{3,10}",
        val in 0i64..100_000,
    ) {
        let obj = serde_json::json!({ key: val });
        let out1 = canonical_json(&obj);
        let out2 = canonical_json(&obj);
        prop_assert_eq!(out1, out2);
    }
}

// ─────────────────────────────────────────────────────────────
// Invariant 5: Different args produce different signatures
// (verifies the args component contributes to the hash)
// ─────────────────────────────────────────────────────────────

proptest! {
    #[test]
    fn different_args_produce_different_signatures(
        session_bytes in proptest::array::uniform16(0u8..),
        step in 0u32..100,
        tool_name in "[a-z]{5}",
        amount_a in 0i64..500_000,
        amount_b in 500_001i64..1_000_000,
    ) {
        let sid   = undolog_types::ids::SessionId::from(uuid::Uuid::from_bytes(session_bytes));
        let args_a = json!({ "amount": amount_a });
        let args_b = json!({ "amount": amount_b });

        let sig_a = CallSignature::compute(&sid, step, &tool_name, &args_a);
        let sig_b = CallSignature::compute(&sid, step, &tool_name, &args_b);

        prop_assert_ne!(sig_a, sig_b);
    }
}

// ─────────────────────────────────────────────────────────────
// Invariant 6: ToolTier label is stable (used in DB + logging)
// ─────────────────────────────────────────────────────────────

#[test]
fn tool_tier_labels_are_stable() {
    use undolog_types::tier::{CompensationDescriptor, ToolTier};

    assert_eq!(ToolTier::Safe.label(), "safe");
    assert_eq!(
        ToolTier::Compensable { compensation: CompensationDescriptor::new("undo", json!({})) }
            .label(),
        "compensable"
    );
    assert_eq!(ToolTier::Irreversible { reason: "dangerous".to_string() }.label(), "irreversible");
}

// ─────────────────────────────────────────────────────────────
// Invariant 7: EffectState terminal states are exhaustive
// ─────────────────────────────────────────────────────────────

#[test]
fn effect_state_terminal_exhaustive() {
    use undolog_types::effect::EffectState;

    let terminal = [
        EffectState::Committed,
        EffectState::Compensated,
        EffectState::CompensationFailed,
        EffectState::Rejected,
        EffectState::Replayed,
    ];

    let non_terminal = [
        EffectState::Pending,
        EffectState::Executing,
        EffectState::Compensating,
        EffectState::Approved,
    ];

    for s in &terminal {
        assert!(s.is_terminal(), "{:?} should be terminal", s);
    }
    for s in &non_terminal {
        assert!(!s.is_terminal(), "{:?} should NOT be terminal", s);
    }
}
