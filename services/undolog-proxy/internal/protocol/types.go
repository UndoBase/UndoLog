// Package protocol defines the shared request and response types used by the proxy.
//
// These types intentionally mirror the Rust engine contracts so the Go proxy
// can exchange wire-compatible requests, responses, and decisions.
package protocol

import (
	"context"
	"encoding/json"
	"errors"
)

// InterceptOutcome tells the proxy how to route an intercepted call.
type InterceptOutcome string

const (
	// InterceptExecute means the tool should run immediately and be committed afterward.
	InterceptExecute InterceptOutcome = "Execute"
	// InterceptReplay means the proxy should return a cached result.
	InterceptReplay InterceptOutcome = "Replay"
	// InterceptAwaitingApproval means the proxy must wait for a human decision.
	InterceptAwaitingApproval InterceptOutcome = "AwaitingApproval"
)

// ToolCall is the canonical tool invocation exchanged between proxy and engine.
type ToolCall struct {
	// OrgID scopes the call to one organization.
	OrgID string `json:"org_id"`
	// SessionID identifies the session that produced the call.
	SessionID string `json:"session_id"`
	// ToolID optionally identifies the registered tool.
	ToolID string `json:"tool_id,omitempty"`
	// ToolName is the logical name of the tool.
	ToolName string `json:"tool_name"`
	// ToolVersion carries optional version metadata for the tool.
	ToolVersion string `json:"tool_version,omitempty"`
	// StepIndex identifies the call order within the session.
	StepIndex uint32 `json:"step_index,omitempty"`
	// Args contains the raw JSON arguments supplied by the caller.
	Args json.RawMessage `json:"args"`
	// Signature is the canonical deduplication key.
	Signature string `json:"signature"`
}

// ToolResult represents the upstream tool execution result.
type ToolResult struct {
	// Success reports whether the execution completed successfully.
	Success bool `json:"success"`
	// Output carries the raw JSON payload returned by the tool.
	Output json.RawMessage `json:"output,omitempty"`
	// Error carries a human-readable error on failure.
	Error string `json:"error,omitempty"`
	// DurationMS records how long the upstream execution took.
	DurationMS uint64 `json:"duration_ms,omitempty"`
}

// InterceptRequest wraps one tool call sent to the engine.
type InterceptRequest struct {
	ToolCall ToolCall `json:"tool_call"`
}

// InterceptResponse tells the proxy what to do next for one intercepted call.
type InterceptResponse struct {
	// Outcome selects the proxy execution path.
	Outcome InterceptOutcome `json:"outcome"`
	// EffectID identifies the effect record in the engine.
	EffectID string `json:"effect_id,omitempty"`
	// ApprovalID identifies the approval request when human approval is required.
	ApprovalID string `json:"approval_id,omitempty"`
	// CachedResult is returned when the outcome is replay.
	CachedResult *ToolResult `json:"cached_result,omitempty"`
	// Error carries an engine-side failure description.
	Error string `json:"error,omitempty"`
}

// CommitRequest reports a successful execution back to the engine.
type CommitRequest struct {
	OrgID     string     `json:"org_id"`
	SessionID string     `json:"session_id"`
	EffectID  string     `json:"effect_id"`
	Result    ToolResult `json:"result"`
}

// FailRequest reports a failed execution back to the engine.
type FailRequest struct {
	OrgID     string `json:"org_id"`
	SessionID string `json:"session_id"`
	EffectID  string `json:"effect_id"`
	Error     string `json:"error"`
}

// ApproveResponse carries execution data returned by the engine after approval.
type ApproveResponse struct {
	EffectID    string          `json:"effect_id"`
	SessionID   string          `json:"session_id"`
	ToolName    string          `json:"tool_name"`
	ToolVersion string          `json:"tool_version,omitempty"`
	Args        json.RawMessage `json:"args"`
}

// ApproveRequest resumes a pending approval request.
type ApproveRequest struct {
	OrgID        string          `json:"org_id"`
	ApprovalID   string          `json:"approval_id"`
	Actor        string          `json:"actor"`
	ApprovedArgs json.RawMessage `json:"approved_args,omitempty"`
}

// RejectRequest rejects a pending approval request.
type RejectRequest struct {
	OrgID      string `json:"org_id"`
	ApprovalID string `json:"approval_id"`
	// Actor identifies the human who rejected the request for the audit trail.
	Actor string `json:"actor"`
}

// ApprovalRecord is a snapshot of an unresolved approval request from the engine.
type ApprovalRecord struct {
	ApprovalID string `json:"approval_id"`
	OrgID      string `json:"org_id"`
	SessionID  string `json:"session_id"`
	EffectID   string `json:"effect_id"`
	ToolName   string `json:"tool_name"`
	// Args are the raw JSON arguments proposed for the call.
	Args json.RawMessage `json:"args"`
	// CreatedAtUnix is the request creation time in Unix milliseconds (UTC).
	CreatedAtUnix int64 `json:"created_at_unix_ms"`
}

// ListPendingApprovalsRequest asks the engine for the unresolved approvals of one org.
type ListPendingApprovalsRequest struct {
	OrgID string `json:"org_id"`
}

// ListPendingApprovalsResponse carries the engine's unresolved approval records.
type ListPendingApprovalsResponse struct {
	Records []ApprovalRecord `json:"approval_records"`
}

// EngineClient is the RPC contract shared by the proxy and engine.
type EngineClient interface {
	Intercept(ctx context.Context, req InterceptRequest) (InterceptResponse, error)
	Commit(ctx context.Context, req CommitRequest) error
	Fail(ctx context.Context, req FailRequest) error
	Approve(ctx context.Context, req ApproveRequest) (ApproveResponse, error)
	Reject(ctx context.Context, req RejectRequest) error
	ListPendingApprovals(ctx context.Context, req ListPendingApprovalsRequest) (ListPendingApprovalsResponse, error)
	Close() error
}

// ErrEngineTransportNotConfigured is returned when the engine client has no transport.
var ErrEngineTransportNotConfigured = errors.New("engine transport not configured")
