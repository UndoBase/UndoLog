// Package lock provides cross-language advisory lock helpers.
//
// The hash must match the Rust engine byte-for-byte so both services derive the
// same advisory lock key for one tool-call signature.
package lock

import "hash/fnv"

// AdvisoryLockKey computes the same 64-bit FNV-1a key used by the Rust engine.
func AdvisoryLockKey(signature string) int64 {
	h := fnv.New64a()
	_, _ = h.Write([]byte(signature))
	return int64(h.Sum64())
}
