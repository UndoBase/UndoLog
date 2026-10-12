package proxy

import (
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/BurntSushi/toml"
)

// fileLayer holds the settings one undolog.toml may define. Every scalar is
// a pointer so an explicit zero stays distinguishable from an absent key:
// an absent key falls through to the environment or the default, an
// explicit zero is kept as configured. File keys mirror the environment
// variable names with the UNDOLOG_PROXY_ prefix removed and lowercased, so
// every variable has a file equivalent with the same unit and value range.
type fileLayer struct {
	ListenAddr                    *string           `toml:"listen_addr"`
	ReadTimeoutSecs               *int              `toml:"read_timeout_secs"`
	WriteTimeoutSecs              *int              `toml:"write_timeout_secs"`
	ShutdownTimeoutSecs           *int              `toml:"shutdown_timeout_secs"`
	RequestTimeoutSecs            *int              `toml:"request_timeout_secs"`
	EngineRetryMaxAttempts        *int              `toml:"engine_retry_max_attempts"`
	EngineRetryBaseMs             *int              `toml:"engine_retry_base_ms"`
	DashboardEventChanSize        *int              `toml:"dashboard_event_chan_size"`
	ApprovalReconcileIntervalSecs *int              `toml:"approval_reconcile_interval_secs"`
	ApprovalRetentionSecs         *int              `toml:"approval_retention_secs"`
	EngineGRPCAddr                *string           `toml:"engine_grpc_addr"`
	EngineAuthToken               *string           `toml:"engine_auth_token"`
	MaxEngineRPCs                 *int              `toml:"max_engine_rpcs"`
	EngineQueueMax                *int              `toml:"engine_queue_max"`
	EngineQueueWaitMs             *int              `toml:"engine_queue_wait_ms"`
	EngineBreakerThreshold        *int              `toml:"engine_breaker_threshold"`
	EngineBreakerResetSecs        *int              `toml:"engine_breaker_reset_secs"`
	UpstreamToolURL               *string           `toml:"upstream_tool_url"`
	LogLevel                      *string           `toml:"log_level"`
	RateLimitRPS                  *tomlNumber       `toml:"rate_limit_rps"`
	RateLimitBurst                *int              `toml:"rate_limit_burst"`
	MaxBodyBytes                  *int64            `toml:"max_body_bytes"`
	ReadHeaderTimeoutSecs         *int              `toml:"read_header_timeout_secs"`
	IdleTimeoutSecs               *int              `toml:"idle_timeout_secs"`
	MaxHeaderBytes                *int              `toml:"max_header_bytes"`
	APIKeys                       map[string]string `toml:"api_keys"`
	Plugins                       []string          `toml:"plugins"`
}

// tomlNumber accepts a TOML integer or floating-point value so a whole
// number such as rate_limit_rps = 50 and a fractional rate such as
// rate_limit_rps = 25.5 both configure the limiter.
type tomlNumber float64

// UnmarshalTOML decodes a TOML integer or float into the rate value.
func (n *tomlNumber) UnmarshalTOML(v any) error {
	switch val := v.(type) {
	case float64:
		*n = tomlNumber(val)
		return nil
	case int64:
		*n = tomlNumber(val)
		return nil
	default:
		return fmt.Errorf("expected a number, got %T", v)
	}
}

// fileKeyKinds states every accepted top-level key and the TOML kind it must
// carry. It is the file format's contract: an unknown key or a wrong kind
// fails startup with the offending key named, so a typo can never be
// silently ignored.
var fileKeyKinds = map[string]string{
	"listen_addr":                      "a string",
	"read_timeout_secs":                "an integer",
	"write_timeout_secs":               "an integer",
	"shutdown_timeout_secs":            "an integer",
	"request_timeout_secs":             "an integer",
	"engine_retry_max_attempts":        "an integer",
	"engine_retry_base_ms":             "an integer",
	"dashboard_event_chan_size":        "an integer",
	"approval_reconcile_interval_secs": "an integer",
	"approval_retention_secs":          "an integer",
	"engine_grpc_addr":                 "a string",
	"engine_auth_token":                "a string",
	"max_engine_rpcs":                  "an integer",
	"engine_queue_max":                 "an integer",
	"engine_queue_wait_ms":             "an integer",
	"engine_breaker_threshold":         "an integer",
	"engine_breaker_reset_secs":        "an integer",
	"upstream_tool_url":                "a string",
	"log_level":                        "a string",
	"rate_limit_rps":                   "a number",
	"rate_limit_burst":                 "an integer",
	"max_body_bytes":                   "an integer",
	"read_header_timeout_secs":         "an integer",
	"idle_timeout_secs":                "an integer",
	"max_header_bytes":                 "an integer",
	"api_keys":                         "a table of strings",
	"plugins":                          "an array of strings",
}

// loadFileConfig reads the TOML file named by envKey (UNDOLOG_PROXY_CONFIG).
// An unset or empty variable selects no file and returns an empty layer, so
// the environment-only path behaves exactly as before. A referenced file
// that cannot be read, parsed, or validated fails startup: a broken safety
// configuration must never be silently replaced by defaults.
func loadFileConfig(envKey string) (*fileLayer, error) {
	path := strings.TrimSpace(os.Getenv(envKey))
	if path == "" {
		return &fileLayer{}, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config file %s: %w", path, err)
	}
	var raw map[string]any
	if err := toml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("config file %s: %w", path, err)
	}
	if err := validateFileKeys(raw); err != nil {
		return nil, fmt.Errorf("config file %s: %w", path, err)
	}
	var f fileLayer
	if err := toml.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("config file %s: %w", path, err)
	}
	if err := f.validate(path); err != nil {
		return nil, err
	}
	return &f, nil
}

// validateFileKeys rejects unknown keys and values of the wrong kind before
// the typed decode, which skips keys the struct does not declare: without
// this pass a misspelled key would be ignored. Keys are checked in sorted
// order so the same file always reports the same key.
func validateFileKeys(raw map[string]any) error {
	keys := make([]string, 0, len(raw))
	for key := range raw {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		want, ok := fileKeyKinds[key]
		if !ok {
			return fmt.Errorf("unknown key %q", key)
		}
		if err := checkKind(key, want, raw[key]); err != nil {
			return err
		}
	}
	return nil
}

// checkKind verifies one value carries the kind its key assigns, checking
// array and table contents element-wise.
func checkKind(key, want string, val any) error {
	switch want {
	case "a string":
		if _, ok := val.(string); !ok {
			return fmt.Errorf("key %q must be %s, got %s", key, want, kindName(val))
		}
	case "an integer":
		if _, ok := val.(int64); !ok {
			return fmt.Errorf("key %q must be %s, got %s", key, want, kindName(val))
		}
	case "a number":
		switch val.(type) {
		case int64, float64:
		default:
			return fmt.Errorf("key %q must be %s, got %s", key, want, kindName(val))
		}
	case "an array of strings":
		list, ok := val.([]any)
		if !ok {
			return fmt.Errorf("key %q must be %s, got %s", key, want, kindName(val))
		}
		for _, item := range list {
			if _, ok := item.(string); !ok {
				return fmt.Errorf("key %q must be %s, got a non-string element", key, want)
			}
		}
	case "a table of strings":
		table, ok := val.(map[string]any)
		if !ok {
			return fmt.Errorf("key %q must be %s, got %s", key, want, kindName(val))
		}
		for name, item := range table {
			if _, ok := item.(string); !ok {
				return fmt.Errorf("key %q entry %q must be a string, got %s", key, name, kindName(item))
			}
		}
	}
	return nil
}

// kindName describes a decoded TOML value for error messages.
func kindName(val any) string {
	switch val.(type) {
	case string:
		return "a string"
	case int64:
		return "an integer"
	case float64:
		return "a number"
	case bool:
		return "a boolean"
	case []any:
		return "an array"
	case map[string]any:
		return "a table"
	default:
		return fmt.Sprintf("a %T", val)
	}
}

// validate enforces the same value ranges the environment parsers enforce
// and trims the api keys table and the plugin list, so a file cannot
// configure a value the environment would refuse. Unlike the environment
// path, a blank api key entry fails startup here instead of being
// dropped: a broken mapping must be loud in a file an operator is
// expected to read.
func (f *fileLayer) validate(path string) error {
	ints := []struct {
		name     string
		val      *int
		positive bool
	}{
		{"read_timeout_secs", f.ReadTimeoutSecs, false},
		{"write_timeout_secs", f.WriteTimeoutSecs, false},
		{"shutdown_timeout_secs", f.ShutdownTimeoutSecs, false},
		{"request_timeout_secs", f.RequestTimeoutSecs, false},
		{"engine_retry_max_attempts", f.EngineRetryMaxAttempts, true},
		{"engine_retry_base_ms", f.EngineRetryBaseMs, false},
		{"dashboard_event_chan_size", f.DashboardEventChanSize, true},
		{"approval_reconcile_interval_secs", f.ApprovalReconcileIntervalSecs, false},
		{"approval_retention_secs", f.ApprovalRetentionSecs, false},
		{"max_engine_rpcs", f.MaxEngineRPCs, false},
		{"engine_queue_max", f.EngineQueueMax, false},
		{"engine_queue_wait_ms", f.EngineQueueWaitMs, false},
		{"engine_breaker_threshold", f.EngineBreakerThreshold, false},
		{"engine_breaker_reset_secs", f.EngineBreakerResetSecs, false},
		{"rate_limit_burst", f.RateLimitBurst, false},
		{"read_header_timeout_secs", f.ReadHeaderTimeoutSecs, false},
		{"idle_timeout_secs", f.IdleTimeoutSecs, false},
		{"max_header_bytes", f.MaxHeaderBytes, true},
	}
	for _, s := range ints {
		if s.val == nil {
			continue
		}
		if *s.val < 0 {
			return fmt.Errorf("config file %s: key %q must be a non-negative integer", path, s.name)
		}
		if s.positive && *s.val == 0 {
			return fmt.Errorf("config file %s: key %q must be a positive integer", path, s.name)
		}
	}
	if f.MaxBodyBytes != nil && *f.MaxBodyBytes <= 0 {
		return fmt.Errorf("config file %s: key %q must be a positive integer", path, "max_body_bytes")
	}
	if f.RateLimitRPS != nil {
		rps := float64(*f.RateLimitRPS)
		if math.IsNaN(rps) || math.IsInf(rps, 0) || rps < 0 {
			return fmt.Errorf("config file %s: key %q must be a finite non-negative number", path, "rate_limit_rps")
		}
	}
	if f.APIKeys != nil {
		if err := f.validateAPIKeys(path); err != nil {
			return err
		}
	}
	if f.Plugins != nil {
		names := make([]string, 0, len(f.Plugins))
		for _, name := range f.Plugins {
			if trimmed := strings.TrimSpace(name); trimmed != "" {
				names = append(names, trimmed)
			}
		}
		f.Plugins = names
	}
	return nil
}

// validateAPIKeys trims every table entry and rejects empty ones, so a
// blank key or organization fails startup with the entry named.
func (f *fileLayer) validateAPIKeys(path string) error {
	names := make([]string, 0, len(f.APIKeys))
	for name := range f.APIKeys {
		names = append(names, name)
	}
	sort.Strings(names)
	trimmed := make(map[string]string, len(f.APIKeys))
	for _, name := range names {
		key := strings.TrimSpace(name)
		org := strings.TrimSpace(f.APIKeys[name])
		if key == "" || org == "" {
			return fmt.Errorf("config file %s: api_keys entry %q must map a key to an organization", path, name)
		}
		trimmed[key] = org
	}
	f.APIKeys = trimmed
	return nil
}

// resolveStr resolves a string setting: a non-empty environment value wins,
// then the file value, then the default.
func resolveStr(envKey string, fileVal *string, def string) string {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return *fileVal
	}
	return getenv(envKey, def)
}

// resolveTrimmed resolves a secret: a non-empty environment value wins,
// then the file value, then empty, and surrounding whitespace never
// becomes part of the secret.
func resolveTrimmed(envKey string, fileVal *string) string {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return strings.TrimSpace(*fileVal)
	}
	return strings.TrimSpace(os.Getenv(envKey))
}

// resolveSecs resolves a seconds setting: a set environment value wins with
// its existing lenient parsing, then the file integer, then the default.
func resolveSecs(envKey string, fileVal *int, def time.Duration) time.Duration {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return time.Duration(*fileVal) * time.Second
	}
	return durationEnv(envKey, def)
}

// resolveMillis resolves a milliseconds setting with the same precedence
// as resolveSecs.
func resolveMillis(envKey string, fileVal *int, def time.Duration) time.Duration {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return time.Duration(*fileVal) * time.Millisecond
	}
	return millisEnv(envKey, def)
}

// resolveInt resolves a positive integer setting with the same precedence
// as resolveSecs.
func resolveInt(envKey string, fileVal *int, def int) int {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return *fileVal
	}
	return intEnv(envKey, def)
}

// resolveInt64 resolves a positive 64-bit integer setting with the same
// precedence as resolveSecs.
func resolveInt64(envKey string, fileVal *int64, def int64) int64 {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return *fileVal
	}
	return int64Env(envKey, def)
}

// strictIntSetting resolves a non-negative integer safety setting: the
// environment when set, strictly parsed, then the file value, then zero.
func strictIntSetting(envKey string, fileVal *int) (int, error) {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return *fileVal, nil
	}
	return strictIntEnv(envKey)
}

// strictMsSetting resolves a non-negative millisecond safety setting with
// the same precedence as strictIntSetting.
func strictMsSetting(envKey string, fileVal *int) (time.Duration, error) {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return time.Duration(*fileVal) * time.Millisecond, nil
	}
	return strictMsEnv(envKey)
}

// strictSecsSetting resolves a non-negative seconds safety setting with the
// same precedence as strictIntSetting.
func strictSecsSetting(envKey string, fileVal *int) (time.Duration, error) {
	if os.Getenv(envKey) == "" && fileVal != nil {
		return time.Duration(*fileVal) * time.Second, nil
	}
	return strictSecsEnv(envKey)
}

// resolveAPIKeys prefers the environment list when set, then the file
// table, then no keys, which LoadConfig rejects at startup.
func resolveAPIKeys(f *fileLayer) map[string]string {
	if strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_API_KEYS")) != "" {
		return parseAPIKeys(os.Getenv("UNDOLOG_PROXY_API_KEYS"))
	}
	if f.APIKeys != nil {
		return f.APIKeys
	}
	return parseAPIKeys("")
}

// resolvePlugins prefers the environment list when set, then the file
// array, then no plugins.
func resolvePlugins(f *fileLayer) []string {
	if strings.TrimSpace(os.Getenv("UNDOLOG_PROXY_PLUGINS")) != "" {
		return parsePlugins(os.Getenv("UNDOLOG_PROXY_PLUGINS"))
	}
	if f.Plugins != nil {
		return f.Plugins
	}
	return parsePlugins("")
}
