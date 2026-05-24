// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// It normalizes request JSON before hashing so the engine can detect duplicate
// tool calls even when argument field ordering differs.
package proxy

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
)

// CanonicalSignature produces a deterministic signature for a tool call.
func CanonicalSignature(toolName string, args json.RawMessage) (string, error) {
	var parsed any
	dec := json.NewDecoder(bytes.NewReader(args))
	dec.UseNumber()
	if err := dec.Decode(&parsed); err != nil {
		return "", fmt.Errorf("decode args: %w", err)
	}
	body := map[string]any{
		"tool_name": toolName,
		"args":      parsed,
	}
	buf := &bytes.Buffer{}
	if err := writeCanonicalJSON(buf, body); err != nil {
		return "", err
	}
	return buf.String(), nil
}

func writeCanonicalJSON(buf *bytes.Buffer, v any) error {
	switch x := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		buf.WriteByte('{')
		for i, k := range keys {
			if i > 0 {
				buf.WriteByte(',')
			}
			keyBytes, _ := json.Marshal(k)
			buf.Write(keyBytes)
			buf.WriteByte(':')
			if err := writeCanonicalJSON(buf, x[k]); err != nil {
				return err
			}
		}
		buf.WriteByte('}')
	case []any:
		buf.WriteByte('[')
		for i, elem := range x {
			if i > 0 {
				buf.WriteByte(',')
			}
			if err := writeCanonicalJSON(buf, elem); err != nil {
				return err
			}
		}
		buf.WriteByte(']')
	default:
		b, err := json.Marshal(x)
		if err != nil {
			return err
		}
		buf.Write(b)
	}
	return nil
}
