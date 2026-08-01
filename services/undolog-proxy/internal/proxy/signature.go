// Package proxy implements the HTTP ingress for the UndoLog MCP interceptor.
//
// It normalizes request JSON before hashing so the engine can detect duplicate
// tool calls even when argument field ordering or numeric spelling differs.
package proxy

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
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
	case json.Number:
		return writeNumber(buf, x)
	default:
		b, err := json.Marshal(x)
		if err != nil {
			return err
		}
		buf.Write(b)
	}
	return nil
}

// writeNumber serialises a numeric token using the same rules as the Rust,
// Python, and TypeScript SDKs (ECMAScript JSON.stringify, RFC 8785): integer
// tokens are preserved verbatim so integers beyond 2^53 stay exact, negative
// zero becomes 0, and floats use fixed notation in [1e-6, 1e21) with
// exponential notation elsewhere (no leading zeros in the exponent).
func writeNumber(buf *bytes.Buffer, n json.Number) error {
	s := n.String()
	if !strings.ContainsAny(s, ".eE") {
		if s == "0" || s == "-0" {
			buf.WriteByte('0')
			return nil
		}
		buf.WriteString(s)
		return nil
	}
	f, err := n.Float64()
	if err != nil {
		return err
	}
	formatted, err := es6Float(f)
	if err != nil {
		return err
	}
	buf.WriteString(formatted)
	return nil
}

// es6Float serialises a finite float exactly as ECMAScript JSON.stringify does.
func es6Float(f float64) (string, error) {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return "", fmt.Errorf("invalid JSON number: %v", f)
	}
	if f == 0 {
		return "0", nil
	}
	abs := math.Abs(f)
	if abs >= 1e-6 && abs < 1e21 {
		return strconv.FormatFloat(f, 'f', -1, 64), nil
	}
	return stripExponentZeros(strconv.FormatFloat(f, 'e', -1, 64)), nil
}

// stripExponentZeros removes leading zeros from the exponent, matching the
// ECMAScript format (1e-07 becomes 1e-7, 1e+07 becomes 1e+7).
func stripExponentZeros(s string) string {
	i := strings.IndexByte(s, 'e')
	if i < 0 {
		return s
	}
	mantissa := s[:i]
	exp := s[i+1:]
	sign := ""
	if exp != "" && (exp[0] == '+' || exp[0] == '-') {
		sign = exp[:1]
		exp = exp[1:]
	}
	exp = strings.TrimLeft(exp, "0")
	if exp == "" {
		exp = "0"
	}
	return mantissa + "e" + sign + exp
}
