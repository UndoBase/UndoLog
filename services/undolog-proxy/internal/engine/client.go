// Package engine provides the gRPC client used to talk to the Rust UndoLog engine.
//
// The client isolates transport concerns from the proxy so tests can inject
// fakes while production uses a retrying gRPC connection to the Rust service.
package engine

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
)

// RetryConfig controls engine connection and Commit/Fail RPC retry behavior.
type RetryConfig struct {
	// MaxAttempts is the maximum number of attempts for a connection or for a
	// retryable Commit/Fail RPC call.
	MaxAttempts int
	// Backoff is the base delay between attempts.
	Backoff time.Duration
}

// Transport describes the RPC surface used by the client and test doubles.
type Transport interface {
	protocol.EngineClient
}

// Client manages the gRPC connection and RPC calls to the Rust engine.
type Client struct {
	address   string
	retry     RetryConfig
	logger    *slog.Logger
	conn      *grpc.ClientConn
	transport Transport
	metrics   *metrics.Registry
}

// SetMetrics wires the registry so engine RPC latency, error counts, and retry
// counts are exposed on /metrics. A nil registry disables instrumentation.
func (c *Client) SetMetrics(reg *metrics.Registry) {
	c.metrics = reg
}

// RPC method labels used in engine RPC metrics.
const (
	rpcMethodIntercept   = "Intercept"
	rpcMethodCommit      = "Commit"
	rpcMethodFail        = "Fail"
	rpcMethodApprove     = "Approve"
	rpcMethodReject      = "Reject"
	rpcMethodListPending = "ListPendingApprovals"
	rpcDurationMetric    = "undolog_proxy_engine_rpc_duration_seconds"
	rpcErrorsMetric      = "undolog_proxy_engine_rpc_errors_total"
	rpcRetriesMetric     = "undolog_proxy_engine_rpc_retries_total"
)

// NewClient constructs a client for the given engine address.
func NewClient(address string, retry RetryConfig, logger *slog.Logger) *Client {
	if logger == nil {
		logger = slog.Default()
	}
	if retry.MaxAttempts <= 0 {
		retry.MaxAttempts = 3
	}
	if retry.Backoff <= 0 {
		retry.Backoff = 100 * time.Millisecond
	}
	return &Client{
		address: address,
		retry:   retry,
		logger:  logger,
	}
}

// NewClientWithTransport constructs a client with an injected transport.
func NewClientWithTransport(address string, retry RetryConfig, transport Transport, logger *slog.Logger) *Client {
	c := NewClient(address, retry, logger)
	c.transport = transport
	return c
}

// Connect opens the gRPC connection with retry and backoff.
func (c *Client) Connect(ctx context.Context) error {
	if c.address == "" {
		return errors.New("engine address is empty")
	}
	if c.conn != nil {
		return nil
	}
	var last error
	for attempt := 1; attempt <= c.retry.MaxAttempts; attempt++ {
		conn, err := grpc.DialContext( //nolint:staticcheck
			ctx,
			c.address,
			grpc.WithTransportCredentials(insecure.NewCredentials()),
			grpc.WithBlock(), //nolint:staticcheck
		)
		if err == nil {
			c.conn = conn
			return nil
		}
		last = err
		c.logger.Warn("engine dial failed", "attempt", attempt, "error", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(c.retry.Backoff * time.Duration(attempt)):
		}
	}
	return last
}

// Close closes the underlying gRPC connection if one was created.
func (c *Client) Close() error {
	if c.conn == nil {
		return nil
	}
	return c.conn.Close()
}

// Conn exposes the underlying gRPC connection (nil until Connect succeeds).
func (c *Client) Conn() *grpc.ClientConn {
	return c.conn
}

// SetTransport sets the transport implementation used for RPC calls.
// Must be called after Connect when using a real gRPC transport.
func (c *Client) SetTransport(t Transport) {
	c.transport = t
}

// Intercept asks the engine how the proxy should route one tool call.
func (c *Client) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	start := time.Now()
	resp, err := call(c, func(t Transport) (protocol.InterceptResponse, error) {
		return t.Intercept(ctx, req)
	})
	c.observeRPC(rpcMethodIntercept, start, err)
	return resp, err
}

// Commit reports a successful execution to the engine.
//
// Commit is retried on transient transport failures because the effect state
// machine makes it idempotent: a retried commit against an already-committed
// effect is a no-op, never a double-apply. A retry cannot duplicate the tool
// call, which already ran before this RPC.
func (c *Client) Commit(ctx context.Context, req protocol.CommitRequest) error {
	start := time.Now()
	err := c.retryVoid(ctx, rpcMethodCommit, func(t Transport) error {
		return t.Commit(ctx, req)
	})
	c.observeRPC(rpcMethodCommit, start, err)
	return err
}

// Fail reports a failed execution to the engine.
//
// Fail is retried on transient failures for the same reason as Commit: the
// engine's executing|approved -> pending transition is idempotent, and the
// effect is already in the state the proxy reports.
func (c *Client) Fail(ctx context.Context, req protocol.FailRequest) error {
	start := time.Now()
	err := c.retryVoid(ctx, rpcMethodFail, func(t Transport) error {
		return t.Fail(ctx, req)
	})
	c.observeRPC(rpcMethodFail, start, err)
	return err
}

// Approve resumes an approval request in the engine and returns execution data.
func (c *Client) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	start := time.Now()
	resp, err := call(c, func(t Transport) (protocol.ApproveResponse, error) {
		return t.Approve(ctx, req)
	})
	c.observeRPC(rpcMethodApprove, start, err)
	return resp, err
}

// Reject rejects an approval request in the engine.
func (c *Client) Reject(ctx context.Context, req protocol.RejectRequest) error {
	start := time.Now()
	err := c.callVoid(func(t Transport) error {
		return t.Reject(ctx, req)
	})
	c.observeRPC(rpcMethodReject, start, err)
	return err
}

// ListPendingApprovals returns the engine's unresolved approvals for one org.
func (c *Client) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	start := time.Now()
	resp, err := call(c, func(t Transport) (protocol.ListPendingApprovalsResponse, error) {
		return t.ListPendingApprovals(ctx, req)
	})
	c.observeRPC(rpcMethodListPending, start, err)
	return resp, err
}

// call checks transport configuration, then delegates to fn.
// Generic over any return type T. Used by Intercept, Approve, and future RPCs.
func call[T any](c *Client, fn func(Transport) (T, error)) (T, error) {
	if c.transport == nil {
		var zero T
		return zero, protocol.ErrEngineTransportNotConfigured
	}
	return fn(c.transport)
}

func (c *Client) callVoid(fn func(Transport) error) error {
	if c.transport == nil {
		return protocol.ErrEngineTransportNotConfigured
	}
	return fn(c.transport)
}

// retryVoid invokes fn and retries transient transport failures with bounded
// backoff. Only Commit and Fail use it: the engine's state-machine transitions
// make both idempotent, so a retried call cannot double-apply. Intercept,
// Approve, Reject, and ListPendingApprovals are intentionally not retried.
func (c *Client) retryVoid(ctx context.Context, method string, fn func(Transport) error) error {
	if c.transport == nil {
		return protocol.ErrEngineTransportNotConfigured
	}
	var last error
	for attempt := 1; attempt <= c.retry.MaxAttempts; attempt++ {
		last = fn(c.transport)
		if last == nil {
			return nil
		}
		if !isRetryableRPCError(last) || attempt == c.retry.MaxAttempts {
			return last
		}
		c.observeRetry(method)
		c.logger.Warn("engine RPC transient failure, retrying", "method", method, "attempt", attempt, "max", c.retry.MaxAttempts, "error", last)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(c.retry.Backoff * time.Duration(attempt)):
		}
	}
	return last
}

// observeRPC records one engine RPC call: a duration histogram always, and an
// error counter when the call failed.
func (c *Client) observeRPC(method string, start time.Time, err error) {
	if c.metrics == nil {
		return
	}
	c.metrics.Histogram(rpcDurationMetric, "Engine RPC call duration in seconds", nil, "method").Observe(time.Since(start).Seconds(), method)
	if err != nil {
		c.metrics.Counter(rpcErrorsMetric, "Engine RPC calls that returned an error", "method").Add(1, method)
	}
}

// observeRetry increments the retry counter for one re-executed engine call.
func (c *Client) observeRetry(method string) {
	if c.metrics == nil {
		return
	}
	c.metrics.Counter(rpcRetriesMetric, "Engine RPC calls that were retried after a transient failure", "method").Add(1, method)
}

// isRetryableRPCError reports whether a Commit or Fail failure is worth
// retrying. Only transport-level failures qualify: an unavailable engine or a
// response lost in transit. Deterministic errors (invalid arguments, a state
// transition the effect already performed) are returned immediately. The
// Unknown code covers connection-level failures that gRPC surfaces without a
// specific status.
func isRetryableRPCError(err error) bool {
	switch status.Code(err) {
	case codes.Unavailable, codes.Aborted, codes.DeadlineExceeded, codes.Unknown:
		return true
	default:
		return false
	}
}
