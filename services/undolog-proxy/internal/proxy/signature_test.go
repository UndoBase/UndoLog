// Package proxy tests the canonical signature number normalisation.
//
// Numbers follow the same rules as the Rust, Python, and TypeScript SDKs
// (ECMAScript JSON.stringify, RFC 8785): negative zero becomes 0, floats use
// fixed notation in [1e-6, 1e21), exponents have no leading zeros, and
// integers beyond 2^53 are preserved exactly.
package proxy

import (
	"encoding/json"
	"math"
	"testing"
)

// TestCanonicalSignatureNormalisesNumbers verifies numerically equal but
// differently spelled numbers produce identical signatures.
func TestCanonicalSignatureNormalisesNumbers(t *testing.T) {
	cases := []struct {
		name  string
		argsA string
		argsB string
	}{
		{"negative zero", `{"v":-0.0}`, `{"v":0}`},
		{"negative zero integer token", `{"v":-0}`, `{"v":0}`},
		{"fixed notation boundary", `{"v":1e-6}`, `{"v":0.000001}`},
		{"exponential boundary", `{"v":1e21}`, `{"v":1e+21}`},
		{"fixed large exponent", `{"v":1e20}`, `{"v":100000000000000000000}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sigA, err := CanonicalSignature("search", json.RawMessage(tc.argsA))
			if err != nil {
				t.Fatalf("signature a: %v", err)
			}
			sigB, err := CanonicalSignature("search", json.RawMessage(tc.argsB))
			if err != nil {
				t.Fatalf("signature b: %v", err)
			}
			if sigA != sigB {
				t.Fatalf("expected %q to match %q", sigA, sigB)
			}
		})
	}
}

// TestCanonicalSignaturePreservesLargeIntegers verifies integers above 2^53
// are emitted verbatim instead of being rounded through float64.
func TestCanonicalSignaturePreservesLargeIntegers(t *testing.T) {
	sig, err := CanonicalSignature("t", json.RawMessage(`{"v":9007199254740993}`))
	if err != nil {
		t.Fatalf("canonical signature: %v", err)
	}
	want := `{"args":{"v":9007199254740993},"tool_name":"t"}`
	if sig != want {
		t.Fatalf("expected %q, got %q", want, sig)
	}
}

// TestCanonicalSignatureRejectsNonFinite verifies overflow to Infinity is
// rejected, matching the SDK contract for non-finite numbers.
func TestCanonicalSignatureRejectsNonFinite(t *testing.T) {
	if _, err := CanonicalSignature("t", json.RawMessage(`{"v":1e400}`)); err == nil {
		t.Fatal("expected an error for a number that overflows to Infinity")
	}
}

// TestES6FloatFormatting verifies the float formatter matches ECMAScript output.
func TestES6FloatFormatting(t *testing.T) {
	cases := []struct {
		in   float64
		want string
	}{
		{-0.0, "0"},
		{0.0, "0"},
		{1e-6, "0.000001"},
		{1e-7, "1e-7"},
		{9.999999e-7, "9.999999e-7"},
		{1e20, "100000000000000000000"},
		{1e21, "1e+21"},
		{1.5e21, "1.5e+21"},
		{5e-324, "5e-324"},
		{-1e-7, "-1e-7"},
		{123.456, "123.456"},
	}
	for _, tc := range cases {
		got, err := es6Float(tc.in)
		if err != nil {
			t.Fatalf("es6Float(%v): %v", tc.in, err)
		}
		if got != tc.want {
			t.Fatalf("es6Float(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// TestES6FloatRejectsNonFinite verifies NaN and Infinity are rejected.
func TestES6FloatRejectsNonFinite(t *testing.T) {
	for _, v := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		if _, err := es6Float(v); err == nil {
			t.Fatalf("expected an error for non-finite %v", v)
		}
	}
}
