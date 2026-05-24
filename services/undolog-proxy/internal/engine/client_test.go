// Package engine tests the gRPC client used to talk to the Rust UndoLog engine.
//
// The tests validate transport delegation and the fail-cleanly behavior when no
// transport is configured.
package engine

import (
	"context"
	"errors"
	"testing"
	"time"

	"undolog-proxy/internal/protocol"
)

type mockTransport struct {
	interceptResp protocol.InterceptResponse
	interceptErr  error
	commitErr     error
	failErr       error
	approveErr    error
	rejectErr     error
	lastCall      protocol.InterceptRequest
}

// Intercept returns the configured fake intercept response.
func (m *mockTransport) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	m.lastCall = req
	return m.interceptResp, m.interceptErr
}

// Commit returns the configured fake commit error.
func (m *mockTransport) Commit(ctx context.Context, req protocol.CommitRequest) error {
	return m.commitErr
}

// Fail returns the configured fake fail error.
func (m *mockTransport) Fail(ctx context.Context, req protocol.FailRequest) error { return m.failErr }

// Approve returns the configured fake approve error.
func (m *mockTransport) Approve(ctx context.Context, req protocol.ApproveRequest) error {
	return m.approveErr
}

// Reject returns the configured fake reject error.
func (m *mockTransport) Reject(ctx context.Context, req protocol.RejectRequest) error {
	return m.rejectErr
}

// Close is a no-op for the mock transport.
func (m *mockTransport) Close() error { return nil }

// TestClientDelegatesToTransport verifies RPC calls are forwarded unchanged.
func TestClientDelegatesToTransport(t *testing.T) {
	mt := &mockTransport{
		interceptResp: protocol.InterceptResponse{
			Outcome:  protocol.InterceptExecute,
			EffectID: "effect-1",
		},
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil)

	resp, err := client.Intercept(context.Background(), protocol.InterceptRequest{
		ToolCall: protocol.ToolCall{ToolName: "search"},
	})
	if err != nil {
		t.Fatalf("intercept returned error: %v", err)
	}
	if resp.EffectID != "effect-1" {
		t.Fatalf("unexpected effect id: %s", resp.EffectID)
	}

	if err := client.Commit(context.Background(), protocol.CommitRequest{EffectID: "effect-1"}); err != nil {
		t.Fatalf("commit returned error: %v", err)
	}
	if err := client.Fail(context.Background(), protocol.FailRequest{EffectID: "effect-1"}); err != nil {
		t.Fatalf("fail returned error: %v", err)
	}
	if err := client.Approve(context.Background(), protocol.ApproveRequest{ApprovalID: "approval-1"}); err != nil {
		t.Fatalf("approve returned error: %v", err)
	}
	if err := client.Reject(context.Background(), protocol.RejectRequest{ApprovalID: "approval-1"}); err != nil {
		t.Fatalf("reject returned error: %v", err)
	}
}

// TestClientWithoutTransportFailsCleanly verifies the missing transport error path.
func TestClientWithoutTransportFailsCleanly(t *testing.T) {
	client := NewClient("ignored", RetryConfig{}, nil)
	_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
	if !errors.Is(err, protocol.ErrEngineTransportNotConfigured) {
		t.Fatalf("expected transport not configured error, got %v", err)
	}
}
