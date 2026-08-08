// Package engine tests the gRPC client used to talk to the Rust UndoLog engine.
//
// The tests validate transport delegation and the fail-cleanly behavior when no
// transport is configured.
package engine

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
)

type mockTransport struct {
	interceptResp  protocol.InterceptResponse
	interceptErr   error
	interceptCalls int
	commitErr      error
	commitErrs     []error
	commitCalls    int
	failErr        error
	failErrs       []error
	failCalls      int
	approveErr     error
	rejectErr      error
	lastCall       protocol.InterceptRequest
}

// Intercept returns the configured fake intercept response and counts calls so
// tests can assert that intercept is never silently retried.
func (m *mockTransport) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	m.interceptCalls++
	m.lastCall = req
	return m.interceptResp, m.interceptErr
}

// Commit returns the next configured fake commit error, or nil once the error
// sequence is exhausted, and counts calls.
func (m *mockTransport) Commit(ctx context.Context, req protocol.CommitRequest) error {
	m.commitCalls++
	if m.commitCalls <= len(m.commitErrs) {
		return m.commitErrs[m.commitCalls-1]
	}
	return m.commitErr
}

// Fail returns the next configured fake fail error, or nil once the error
// sequence is exhausted, and counts calls.
func (m *mockTransport) Fail(ctx context.Context, req protocol.FailRequest) error {
	m.failCalls++
	if m.failCalls <= len(m.failErrs) {
		return m.failErrs[m.failCalls-1]
	}
	return m.failErr
}

// Approve returns the configured fake approve response and error.
func (m *mockTransport) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	return protocol.ApproveResponse{}, m.approveErr
}

// Reject returns the configured fake reject error.
func (m *mockTransport) Reject(ctx context.Context, req protocol.RejectRequest) error {
	return m.rejectErr
}

// ListPendingApprovals returns an empty pending list for the mock transport.
func (m *mockTransport) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	return protocol.ListPendingApprovalsResponse{}, nil
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
	if _, err := client.Approve(context.Background(), protocol.ApproveRequest{ApprovalID: "approval-1"}); err != nil {
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

// TestClientCommitRetriesTransientFailure verifies Commit is retried once when
// the engine is unavailable, then succeeds on the second attempt.
func TestClientCommitRetriesTransientFailure(t *testing.T) {
	mt := &mockTransport{
		commitErrs: []error{status.Error(codes.Unavailable, "engine restarting")},
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 3, Backoff: time.Millisecond}, mt, nil)

	if err := client.Commit(context.Background(), protocol.CommitRequest{EffectID: "effect-1"}); err != nil {
		t.Fatalf("commit should succeed after a retry, got %v", err)
	}
	if mt.commitCalls != 2 {
		t.Fatalf("expected 2 commit calls (1 failure + 1 success), got %d", mt.commitCalls)
	}
}

// TestClientFailRetriesTransientFailure verifies Fail is retried across a
// transient engine error the same way Commit is.
func TestClientFailRetriesTransientFailure(t *testing.T) {
	mt := &mockTransport{
		failErrs: []error{status.Error(codes.Unavailable, "engine restarting")},
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 3, Backoff: time.Millisecond}, mt, nil)

	if err := client.Fail(context.Background(), protocol.FailRequest{EffectID: "effect-1"}); err != nil {
		t.Fatalf("fail should succeed after a retry, got %v", err)
	}
	if mt.failCalls != 2 {
		t.Fatalf("expected 2 fail calls (1 failure + 1 success), got %d", mt.failCalls)
	}
}

// TestClientCommitDoesNotRetryNonRetryable verifies a deterministic engine
// error is returned immediately without further attempts.
func TestClientCommitDoesNotRetryNonRetryable(t *testing.T) {
	mt := &mockTransport{
		commitErrs: []error{status.Error(codes.Internal, "state transition rejected")},
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 3, Backoff: time.Millisecond}, mt, nil)

	err := client.Commit(context.Background(), protocol.CommitRequest{EffectID: "effect-1"})
	if err == nil {
		t.Fatal("expected commit to fail")
	}
	if mt.commitCalls != 1 {
		t.Fatalf("non-retryable errors must not be retried, got %d calls", mt.commitCalls)
	}
}

// TestClientCommitStopsAfterMaxAttempts verifies retries are bounded by the
// configured attempt count.
func TestClientCommitStopsAfterMaxAttempts(t *testing.T) {
	mt := &mockTransport{
		commitErrs: []error{
			status.Error(codes.Unavailable, "engine restarting"),
			status.Error(codes.Unavailable, "engine restarting"),
		},
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 2, Backoff: time.Millisecond}, mt, nil)

	err := client.Commit(context.Background(), protocol.CommitRequest{EffectID: "effect-1"})
	if err == nil {
		t.Fatal("expected commit to fail after exhausting attempts")
	}
	if mt.commitCalls != 2 {
		t.Fatalf("expected 2 commit calls (the attempt budget), got %d", mt.commitCalls)
	}
}

// TestClientInterceptNotRetried verifies Intercept is not silently retried on a
// transient failure: the error propagates immediately.
func TestClientInterceptNotRetried(t *testing.T) {
	mt := &mockTransport{
		interceptErr: status.Error(codes.Unavailable, "engine restarting"),
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 3, Backoff: time.Millisecond}, mt, nil)

	_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
	if err == nil {
		t.Fatal("expected intercept to fail")
	}
	if mt.interceptCalls != 1 {
		t.Fatalf("intercept must not be retried, got %d calls", mt.interceptCalls)
	}
}

// TestClientMetricsObserved verifies successful RPCs contribute a duration
// histogram but no error counter to the shared registry.
func TestClientMetricsObserved(t *testing.T) {
	reg := metrics.NewRegistry()
	mt := &mockTransport{}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 2, Backoff: time.Millisecond}, mt, nil)
	client.SetMetrics(reg)

	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); err != nil {
		t.Fatalf("intercept: %v", err)
	}

	rendered := reg.Render()
	if !strings.Contains(rendered, `undolog_proxy_engine_rpc_duration_seconds_bucket{method="Intercept",le="+Inf"} 1`) {
		t.Errorf("expected an Intercept duration observation, got:\n%s", rendered)
	}
	if strings.Contains(rendered, rpcErrorsMetric) {
		t.Errorf("no errors expected on success, got:\n%s", rendered)
	}
}

// TestClientMetricsErrorsAndRetries verifies a retried Commit records the retry
// counter and a deterministic failure records an error counter.
func TestClientMetricsErrorsAndRetries(t *testing.T) {
	reg := metrics.NewRegistry()
	mt := &mockTransport{
		commitErrs: []error{status.Error(codes.Unavailable, "engine restarting")},
	}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 2, Backoff: time.Millisecond}, mt, nil)
	client.SetMetrics(reg)

	if err := client.Commit(context.Background(), protocol.CommitRequest{EffectID: "e-1"}); err != nil {
		t.Fatalf("commit should recover after one retry: %v", err)
	}
	rendered := reg.Render()
	if !strings.Contains(rendered, `undolog_proxy_engine_rpc_retries_total{method="Commit"} 1`) {
		t.Errorf("expected one Commit retry, got:\n%s", rendered)
	}

	mt.failErrs = []error{status.Error(codes.Internal, "state transition rejected")}
	if err := client.Fail(context.Background(), protocol.FailRequest{EffectID: "e-2"}); err == nil {
		t.Fatal("expected fail to error")
	}
	rendered = reg.Render()
	if !strings.Contains(rendered, `undolog_proxy_engine_rpc_errors_total{method="Fail"} 1`) {
		t.Errorf("expected one Fail error, got:\n%s", rendered)
	}
}

// TestWithRequestIDEmptyIsPassthrough verifies an empty request ID does not add
// outgoing metadata.
func TestWithRequestIDEmptyIsPassthrough(t *testing.T) {
	ctx := withTracingMetadata(WithRequestID(context.Background(), ""))
	md, ok := metadata.FromOutgoingContext(ctx)
	if ok && len(md.Get("x-request-id")) > 0 {
		t.Fatalf("expected no outgoing metadata, got %v", md)
	}
}

// TestWithTracingMetadataAttachesRequestID verifies a non-empty request ID
// becomes an outgoing x-request-id metadata pair for the engine call.
func TestWithTracingMetadataAttachesRequestID(t *testing.T) {
	ctx := withTracingMetadata(WithRequestID(context.Background(), "req-abc"))
	md, ok := metadata.FromOutgoingContext(ctx)
	if !ok {
		t.Fatal("expected outgoing metadata")
	}
	ids := md.Get("x-request-id")
	if len(ids) != 1 || ids[0] != "req-abc" {
		t.Fatalf("expected x-request-id metadata req-abc, got %v", ids)
	}
}
