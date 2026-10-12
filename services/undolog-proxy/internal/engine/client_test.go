// Package engine tests the gRPC client used to talk to the Rust UndoLog engine.
//
// The tests validate transport delegation and the fail-cleanly behavior when no
// transport is configured.
package engine

import (
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
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

// TestClientWithoutTransportFailsCleanly verifies the missing transport error
// path: a client built without a transport factory returns the sentinel error
// instead of panicking or dialing into the void.
func TestClientWithoutTransportFailsCleanly(t *testing.T) {
	client := NewClientWithTransport("ignored", RetryConfig{}, nil, nil)
	_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
	if !errors.Is(err, protocol.ErrEngineTransportNotConfigured) {
		t.Fatalf("expected transport not configured error, got %v", err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("close should be a no-op without a connection, got %v", err)
	}
}

// TestClientDialsLazily verifies the engine connection is created on the first
// RPC, not at construction, and that a failed dial is retried on the next RPC
// rather than cached. The injected dialer only ever fails, so every Intercept
// pays one dial attempt and the call surfaces the dial error.
func TestClientDialsLazily(t *testing.T) {
	calls := 0
	client := NewClient("127.0.0.1:1", RetryConfig{}, nil)
	client.dial = func(_ context.Context, address string) (*grpc.ClientConn, error) {
		calls++
		return nil, errors.New("dial failed")
	}
	client.mu.Lock()
	if client.conn != nil {
		client.mu.Unlock()
		t.Fatal("no connection should exist before the first RPC")
	}
	client.mu.Unlock()

	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); err == nil {
		t.Fatal("expected the first intercept to fail while the engine is down")
	}
	if calls != 1 {
		t.Fatalf("expected exactly one dial for the first RPC, got %d", calls)
	}
	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); err == nil {
		t.Fatal("expected the second intercept to fail while the engine is down")
	}
	if calls != 2 {
		t.Fatalf("expected a fresh dial attempt for the second RPC, got %d", calls)
	}
}

// TestClientCarriesChannelThroughConnectionFailure verifies a connection-level
// RPC failure surfaces the error to the caller and the client keeps the same
// channel (it does not spin up a fresh connection per failure, which would leak
// channels during an engine outage). The actual reconnection is exercised end
// to end by the restart integration test in client_reconnect_test.go.
func TestClientCarriesChannelThroughConnectionFailure(t *testing.T) {
	channel, err := grpc.NewClient(
		"127.0.0.1:1",
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	defer func() { _ = channel.Close() }()

	mt := &mockTransport{interceptErr: status.Error(codes.Unavailable, "engine restarting")}
	client := NewClientWithTransport("127.0.0.1:1", RetryConfig{}, mt, nil)
	client.mu.Lock()
	client.conn = channel
	client.mu.Unlock()

	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); status.Code(err) != codes.Unavailable {
		t.Fatalf("expected the Unavailable outage to surface, got %v", err)
	}

	client.mu.Lock()
	kept := client.conn
	client.mu.Unlock()
	if kept != channel {
		t.Fatal("the client must keep its channel across a failed RPC")
	}
}

// TestIsConnectionLossClassifiesCodes verifies which error codes signal a
// transport loss (prompting a backoff reset) versus application-level results
// that must not.
func TestIsConnectionLossClassifiesCodes(t *testing.T) {
	cases := []struct {
		code codes.Code
		want bool
	}{
		{code: codes.Unavailable, want: true},
		{code: codes.Unknown, want: true},
		{code: codes.Aborted, want: false},
		{code: codes.DeadlineExceeded, want: false},
		{code: codes.Internal, want: false},
		{code: codes.InvalidArgument, want: false},
		{code: codes.Unauthenticated, want: false},
		{code: codes.OK, want: false},
	}
	for _, tc := range cases {
		got := isConnectionLoss(status.Error(tc.code, "classify me"))
		if got != tc.want {
			t.Errorf("isConnectionLoss(%s) = %v, want %v", tc.code, got, tc.want)
		}
	}
}

// TestClientClosedDoesNotResurrect verifies a closed client fails RPCs with
// ErrClientClosed and never dials again, so a late reconciliation tick after
// shutdown cannot reopen the connection.
func TestClientClosedDoesNotResurrect(t *testing.T) {
	calls := 0
	client := NewClient("127.0.0.1:1", RetryConfig{}, nil)
	client.dial = func(_ context.Context, _ string) (*grpc.ClientConn, error) {
		calls++
		return nil, errors.New("dial failed")
	}

	if err := client.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); !errors.Is(err, protocol.ErrEngineClientClosed) {
		t.Fatalf("expected ErrEngineClientClosed after Close, got %v", err)
	}
	if calls != 0 {
		t.Fatalf("a closed client must not dial, got %d dials", calls)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("Close must be idempotent, got %v", err)
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

// engineAuthToken is the shared secret the token-checking stub engine expects.
const engineAuthToken = "s3cret-engine-token"

// recordedCall is one RPC as the token-checking stub engine saw it.
type recordedCall struct {
	method string
	auth   []string
}

// authRecorder stands in for the engine's token interceptor. It records the
// `authorization` metadata of every unary RPC and, when expected is set,
// rejects a call without the matching bearer with UNAUTHENTICATED and the
// engine's message, before the handler runs.
type authRecorder struct {
	expected string
	mu       sync.Mutex
	calls    []recordedCall
}

// intercept is the unary server interceptor installed on the stub engine.
func (a *authRecorder) intercept(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
	var auth []string
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		auth = md.Get(authorizationKey)
	}
	a.mu.Lock()
	a.calls = append(a.calls, recordedCall{method: info.FullMethod, auth: auth})
	a.mu.Unlock()
	if a.expected != "" && (len(auth) != 1 || auth[0] != bearerPrefix+a.expected) {
		return nil, status.Error(codes.Unauthenticated, "missing or invalid credentials: expected `authorization` metadata as `Bearer <token>`")
	}
	return handler(ctx, req)
}

// recorded returns a snapshot of the calls seen so far.
func (a *authRecorder) recorded() []recordedCall {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]recordedCall(nil), a.calls...)
}

// startAuthEngine runs the stub engine behind an authRecorder on address.
func startAuthEngine(t *testing.T, address, expected string) (*grpc.Server, *authRecorder, string) {
	t.Helper()
	rec := &authRecorder{expected: expected}
	srv, _, addr := startEngine(t, address, grpc.UnaryInterceptor(rec.intercept))
	t.Cleanup(srv.Stop)
	return srv, rec, addr
}

// rpcContext bounds one test RPC so a broken channel fails instead of hanging.
func rpcContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	return ctx
}

// callEveryRPC issues each of the six engine RPCs once. Only Intercept is
// implemented by the stub, so the others answer Unimplemented; the errors are
// returned per method so callers can tell an auth rejection from that.
func callEveryRPC(ctx context.Context, client *Client) map[string]error {
	errs := map[string]error{}
	_, errs[rpcMethodIntercept] = client.Intercept(ctx, interceptRequest())
	errs[rpcMethodCommit] = client.Commit(ctx, protocol.CommitRequest{EffectID: "effect-1"})
	errs[rpcMethodFail] = client.Fail(ctx, protocol.FailRequest{EffectID: "effect-1"})
	_, errs[rpcMethodApprove] = client.Approve(ctx, protocol.ApproveRequest{ApprovalID: "approval-1"})
	errs[rpcMethodReject] = client.Reject(ctx, protocol.RejectRequest{ApprovalID: "approval-1"})
	_, errs[rpcMethodListPending] = client.ListPendingApprovals(ctx, protocol.ListPendingApprovalsRequest{OrgID: "org-1"})
	return errs
}

// TestClientPresentsTokenOnEveryRPC verifies a configured token reaches the
// engine as `Bearer <token>` on all six RPCs, and that the enforcing engine
// lets each of them through to its handler.
func TestClientPresentsTokenOnEveryRPC(t *testing.T) {
	_, rec, addr := startAuthEngine(t, "127.0.0.1:0", engineAuthToken)
	client := NewClient(addr, RetryConfig{MaxAttempts: 1}, nil, WithAuthToken(engineAuthToken))
	defer func() { _ = client.Close() }()

	for method, err := range callEveryRPC(rpcContext(t), client) {
		if method == rpcMethodIntercept && err != nil {
			t.Fatalf("authenticated intercept failed: %v", err)
		}
		if code := status.Code(err); code == codes.Unauthenticated {
			t.Fatalf("%s was rejected despite carrying the token: %v", method, err)
		}
	}

	calls := rec.recorded()
	if len(calls) != 6 {
		t.Fatalf("expected the engine to see 6 RPCs, got %d", len(calls))
	}
	for _, call := range calls {
		if len(call.auth) != 1 || call.auth[0] != "Bearer "+engineAuthToken {
			t.Errorf("%s carried authorization %q, want one bearer value", call.method, call.auth)
		}
	}
}

// TestClientWithoutTokenSendsNoAuthorization verifies that no option and an
// explicitly empty token both leave the outgoing metadata without an
// `authorization` key, which is the behaviour before this setting existed.
func TestClientWithoutTokenSendsNoAuthorization(t *testing.T) {
	cases := []struct {
		name string
		opts []Option
	}{
		{name: "no option"},
		{name: "empty token", opts: []Option{WithAuthToken("")}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, rec, addr := startAuthEngine(t, "127.0.0.1:0", "")
			client := NewClient(addr, RetryConfig{MaxAttempts: 1}, nil, tc.opts...)
			defer func() { _ = client.Close() }()

			if _, err := client.Intercept(rpcContext(t), interceptRequest()); err != nil {
				t.Fatalf("intercept against an open engine: %v", err)
			}
			calls := rec.recorded()
			if len(calls) != 1 {
				t.Fatalf("expected one RPC, got %d", len(calls))
			}
			if calls[0].auth != nil {
				t.Fatalf("expected no authorization metadata, got %q", calls[0].auth)
			}
		})
	}
}

// TestClientExplainsEngineAuthRejection verifies an UNAUTHENTICATED answer
// keeps its gRPC code, names the proxy setting to fix, keeps the engine's
// explanation, echoes no token, and is never retried.
func TestClientExplainsEngineAuthRejection(t *testing.T) {
	const wrongToken = "hunter2-wrong-token"
	cases := []struct {
		name string
		opts []Option
	}{
		{name: "missing token"},
		{name: "wrong token", opts: []Option{WithAuthToken(wrongToken)}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, rec, addr := startAuthEngine(t, "127.0.0.1:0", engineAuthToken)
			client := NewClient(addr, RetryConfig{MaxAttempts: 3, Backoff: time.Millisecond}, nil, tc.opts...)
			defer func() { _ = client.Close() }()

			for method, err := range callEveryRPC(rpcContext(t), client) {
				if status.Code(err) != codes.Unauthenticated {
					t.Fatalf("%s: expected Unauthenticated, got %v", method, err)
				}
				msg := err.Error()
				for _, want := range []string{"UNDOLOG_PROXY_ENGINE_AUTH_TOKEN", "UNDOLOG_ENGINE_AUTH_TOKEN", "authorization"} {
					if !strings.Contains(msg, want) {
						t.Errorf("%s: error %q must mention %s", method, msg, want)
					}
				}
				for _, secret := range []string{engineAuthToken, wrongToken} {
					if strings.Contains(msg, secret) {
						t.Errorf("%s: error %q must not echo a token", method, msg)
					}
				}
			}
			if calls := rec.recorded(); len(calls) != 6 {
				t.Fatalf("auth rejections must not be retried: expected 6 RPCs, got %d", len(calls))
			}
		})
	}
}

// TestClientPresentsTokenAfterEngineRestart verifies the credential is
// attached per RPC rather than per connection: after the engine goes away and
// comes back on the same address, the reconnected channel still authenticates.
func TestClientPresentsTokenAfterEngineRestart(t *testing.T) {
	srv, _, addr := startAuthEngine(t, "127.0.0.1:0", engineAuthToken)
	client := NewClient(addr, RetryConfig{MaxAttempts: 1}, nil, WithAuthToken(engineAuthToken))
	defer func() { _ = client.Close() }()

	intercept := func() error {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_, err := client.Intercept(ctx, interceptRequest())
		return err
	}
	if err := intercept(); err != nil {
		t.Fatalf("intercept before the restart: %v", err)
	}

	srv.Stop()
	eventually(t, 5*time.Second, "engine outage surfaced", func() error {
		if intercept() == nil {
			return errors.New("expected the RPC to fail while the engine is down")
		}
		return nil
	})

	_, rec, _ := startAuthEngine(t, addr, engineAuthToken)
	eventually(t, 15*time.Second, "authenticated recovery", intercept)
	for _, call := range rec.recorded() {
		if len(call.auth) != 1 || call.auth[0] != "Bearer "+engineAuthToken {
			t.Fatalf("restarted engine saw authorization %q, want the bearer token", call.auth)
		}
	}
}

// TestExplainAuthFailurePassesOtherErrorsThrough verifies only
// UNAUTHENTICATED is rewritten: nil and every other error come back as the
// identical value, so existing status mapping is untouched.
func TestExplainAuthFailurePassesOtherErrorsThrough(t *testing.T) {
	if err := explainAuthFailure(nil); err != nil {
		t.Fatalf("nil must stay nil, got %v", err)
	}
	for _, original := range []error{
		status.Error(codes.Unavailable, "engine restarting"),
		status.Error(codes.PermissionDenied, "org mismatch"),
		protocol.ErrEngineClientClosed,
	} {
		if got := explainAuthFailure(original); got != original {
			t.Errorf("expected %v to pass through unchanged, got %v", original, got)
		}
	}
}

// blockingTransport holds every Intercept call until its release channel is
// closed, counts the calls that reach it, and answers with the configured
// failure. The counts prove rejected calls never touch the engine.
type blockingTransport struct {
	release chan struct{}
	failure error
	calls   atomic.Int32
}

func (b *blockingTransport) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	b.calls.Add(1)
	<-b.release
	return protocol.InterceptResponse{}, b.failure
}

func (b *blockingTransport) Commit(ctx context.Context, req protocol.CommitRequest) error {
	return b.failure
}

func (b *blockingTransport) Fail(ctx context.Context, req protocol.FailRequest) error {
	return b.failure
}

func (b *blockingTransport) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	return protocol.ApproveResponse{}, b.failure
}

func (b *blockingTransport) Reject(ctx context.Context, req protocol.RejectRequest) error {
	return b.failure
}

// ListPendingApprovals passes through unblocked: background reconciliation
// is exempt from the guard by design.
func (b *blockingTransport) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	return protocol.ListPendingApprovalsResponse{}, nil
}

func (b *blockingTransport) Close() error { return nil }

// newPassingTransport returns a transport whose calls are never held.
func newPassingTransport() *blockingTransport {
	mt := &blockingTransport{release: make(chan struct{})}
	close(mt.release)
	return mt
}

// TestGuardRejectsWhenBreakerOpen verifies a threshold run of transport
// failures opens the breaker, later calls are rejected with the busy
// sentinel before the engine, the rejection is labelled on the metrics,
// and reconciliation calls stay exempt.
func TestGuardRejectsWhenBreakerOpen(t *testing.T) {
	mt := &blockingTransport{release: make(chan struct{})}
	close(mt.release)
	mt.failure = status.Error(codes.Unavailable, "engine down")

	registry := metrics.NewRegistry()
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{
		BreakerThreshold: 1,
		BreakerReset:     30 * time.Second,
	}))
	client.SetMetrics(registry)

	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); err == nil || errors.Is(err, protocol.ErrEngineBusy) {
		t.Fatalf("first call must reach the engine and fail, got %v", err)
	}
	if got := mt.calls.Load(); got != 1 {
		t.Fatalf("engine calls = %d, want 1", got)
	}

	_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
	if !errors.Is(err, protocol.ErrEngineBusy) {
		t.Fatalf("call against an open breaker = %v, want the busy sentinel", err)
	}
	if got := mt.calls.Load(); got != 1 {
		t.Fatalf("rejected call must not touch the engine, calls = %d", got)
	}

	if _, err := client.ListPendingApprovals(context.Background(), protocol.ListPendingApprovalsRequest{}); err != nil {
		t.Fatalf("ListPendingApprovals must stay exempt from the breaker: %v", err)
	}

	out := registry.Render()
	for _, want := range []string{
		`undolog_proxy_engine_rejections_total{reason="breaker"} 1`,
		"undolog_proxy_engine_breaker_state 2",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("metrics missing %q:\n%s", want, out)
		}
	}
}

// TestGuardGateBoundsConcurrentCalls verifies the N+1st in-flight call is
// rejected before the engine while the gate is full, and the freed slot is
// reusable afterwards.
func TestGuardGateBoundsConcurrentCalls(t *testing.T) {
	mt := &blockingTransport{release: make(chan struct{})}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{
		MaxInFlight: 1,
	}))

	firstDone := make(chan error, 1)
	go func() {
		_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
		firstDone <- err
	}()
	waitUntil(t, func() bool { return mt.calls.Load() == 1 })

	_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
	if !errors.Is(err, protocol.ErrEngineBusy) {
		t.Fatalf("call at the cap = %v, want the busy sentinel", err)
	}
	if got := mt.calls.Load(); got != 1 {
		t.Fatalf("rejected call must not touch the engine, calls = %d", got)
	}

	close(mt.release)
	if err := <-firstDone; err != nil {
		t.Fatalf("held call must complete once the slot frees: %v", err)
	}
	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); err != nil {
		t.Fatalf("call after the release must be admitted: %v", err)
	}
}

// TestGuardQueueAdmitsWaiterWhenSlotFrees verifies a queued call completes
// once a slot frees inside the wait budget.
func TestGuardQueueAdmitsWaiterWhenSlotFrees(t *testing.T) {
	mt := &blockingTransport{release: make(chan struct{})}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{
		MaxInFlight: 1,
		QueueMax:    1,
		QueueWait:   2 * time.Second,
	}))

	firstDone := make(chan error, 1)
	go func() {
		_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
		firstDone <- err
	}()
	waitUntil(t, func() bool { return mt.calls.Load() == 1 })

	secondDone := make(chan error, 1)
	go func() {
		_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
		secondDone <- err
	}()
	waitUntil(t, func() bool { return client.guard.queue.depth() == 1 })

	close(mt.release)
	if err := <-firstDone; err != nil {
		t.Fatalf("held call must complete once the slot frees: %v", err)
	}
	if err := <-secondDone; err != nil {
		t.Fatalf("queued call must be admitted after the release: %v", err)
	}
	if got := mt.calls.Load(); got != 2 {
		t.Fatalf("engine calls = %d, want 2", got)
	}
}

// TestGuardQueueRejectsWhenFull verifies a call over the queue depth is
// rejected with the busy sentinel and the reason names the queue.
func TestGuardQueueRejectsWhenFull(t *testing.T) {
	mt := &blockingTransport{release: make(chan struct{})}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{
		MaxInFlight: 1,
		QueueMax:    1,
		QueueWait:   2 * time.Second,
	}))

	firstDone := make(chan error, 1)
	go func() {
		_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
		firstDone <- err
	}()
	waitUntil(t, func() bool { return mt.calls.Load() == 1 })

	waiterDone := make(chan error, 1)
	go func() {
		_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
		waiterDone <- err
	}()
	waitUntil(t, func() bool { return client.guard.queue.depth() == 1 })

	_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
	if !errors.Is(err, protocol.ErrEngineBusy) {
		t.Fatalf("call over the queue depth = %v, want the busy sentinel", err)
	}
	if got := mt.calls.Load(); got != 1 {
		t.Fatalf("rejected call must not touch the engine, calls = %d", got)
	}

	close(mt.release)
	if err := <-firstDone; err != nil {
		t.Fatalf("held call must complete once the slot frees: %v", err)
	}
	if err := <-waiterDone; err != nil {
		t.Fatalf("queued call must be admitted after the release: %v", err)
	}
}

// TestGuardQueueWaitExpires verifies a call that cannot get a slot inside
// the wait budget is rejected with the busy sentinel instead of waiting
// behind a stalled engine.
func TestGuardQueueWaitExpires(t *testing.T) {
	mt := &blockingTransport{release: make(chan struct{})}
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{
		MaxInFlight: 1,
		QueueMax:    2,
		QueueWait:   50 * time.Millisecond,
	}))

	firstDone := make(chan error, 1)
	go func() {
		_, err := client.Intercept(context.Background(), protocol.InterceptRequest{})
		firstDone <- err
	}()
	waitUntil(t, func() bool { return mt.calls.Load() == 1 })

	if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); !errors.Is(err, protocol.ErrEngineBusy) {
		t.Fatalf("call after the wait budget = %v, want the busy sentinel", err)
	}
	if got := mt.calls.Load(); got != 1 {
		t.Fatalf("rejected call must not touch the engine, calls = %d", got)
	}

	close(mt.release)
	if err := <-firstDone; err != nil {
		t.Fatalf("held call must complete once the slot frees: %v", err)
	}
}

// TestGuardDisabledMatchesUnconfigured verifies an all-zero backpressure
// config runs no queue, no gate, and no breaker: every call reaches the
// engine and no guard metric is registered.
func TestGuardDisabledMatchesUnconfigured(t *testing.T) {
	mt := newPassingTransport()
	registry := metrics.NewRegistry()
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{}))
	client.SetMetrics(registry)

	for i := 0; i < 5; i++ {
		if _, err := client.Intercept(context.Background(), protocol.InterceptRequest{}); err != nil {
			t.Fatalf("call %d with the guard disabled: %v", i, err)
		}
	}
	if got := mt.calls.Load(); got != 5 {
		t.Fatalf("engine calls = %d, want 5", got)
	}
	if out := registry.Render(); strings.Contains(out, "undolog_proxy_engine_inflight") {
		t.Fatalf("disabled guard must register no metrics:\n%s", out)
	}
}

// TestGuardPublishesGaugesAtStartup verifies the gauges appear on the
// registry the moment metrics are attached, before any traffic, so
// dashboards see zeroed series instead of absent ones.
func TestGuardPublishesGaugesAtStartup(t *testing.T) {
	mt := newPassingTransport()
	client := NewClientWithTransport("ignored", RetryConfig{MaxAttempts: 1, Backoff: time.Millisecond}, mt, nil, WithBackpressure(BackpressureConfig{
		MaxInFlight:      4,
		QueueMax:         8,
		QueueWait:        100 * time.Millisecond,
		BreakerThreshold: 3,
		BreakerReset:     30 * time.Second,
	}))
	registry := metrics.NewRegistry()
	client.SetMetrics(registry)

	out := registry.Render()
	for _, want := range []string{
		"undolog_proxy_engine_inflight 0",
		"undolog_proxy_engine_queue_depth 0",
		"undolog_proxy_engine_breaker_state 0",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("metrics missing %q:\n%s", want, out)
		}
	}
}
