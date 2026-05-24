// Package lock tests the advisory lock helper used by the proxy and engine.
//
// It ensures the Go helper stays byte-for-byte compatible with the Rust FNV-1a
// advisory lock derivation.
package lock

import (
	"hash/fnv"
	"testing"
)

// TestAdvisoryLockKeyMatchesFNV1a verifies the Go helper matches the FNV-1a hash.
func TestAdvisoryLockKeyMatchesFNV1a(t *testing.T) {
	cases := []string{
		"",
		"abc",
		"tool:fetch_url|session:1|step:9",
		`{"tool_name":"search","args":{"q":"undo log"}}`,
	}

	for _, tc := range cases {
		want := fnv.New64a()
		_, _ = want.Write([]byte(tc))
		if got := AdvisoryLockKey(tc); got != int64(want.Sum64()) {
			t.Fatalf("signature %q: got %d want %d", tc, got, int64(want.Sum64()))
		}
	}
}
