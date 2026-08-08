// Package engine provides the gRPC client used to talk to the Rust UndoLog engine.
//
// The client isolates transport concerns from the proxy so tests can inject
// fakes while production connects lazily to the Rust service. The connection is
// established on the first RPC and reconnected automatically by gRPC whenever
// the engine restarts or is slow to become ready, so the proxy never has to
// block on a bootstrap dial.
package engine

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/backoff"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	"undolog-proxy/internal/metrics"
	"undolog-proxy/internal/protocol"
)

// RetryConfig controls Commit/Fail RPC retry behavior.
//
// The initial connection is lazy and non-blocking, so it is not part of the
// retry budget. Only the idempotent Commit and Fail calls retry, and only on
// transient transport failures.
type RetryConfig struct {
	// MaxAttempts is the maximum number of attempts for a retryable Commit/Fail RPC call.
	MaxAttempts int
	// Backoff is the base delay between attempts.
	Backoff time.Duration
}

// Transport describes the RPC surface used by the client and test doubles.
type Transport interface {
	protocol.EngineClient
}

// reconnectBackoff bounds the gRPC reconnection schedule so a restarted engine
// is reachable again within seconds instead of sitting in the default backoff,
// which grows to minutes. The proxy stops RPC calls at the caller's deadline, so
// the base delay only paces the background reconnect attempts.
var reconnectBackoff = backoff.Config{
	BaseDelay:  100 * time.Millisecond,
	Multiplier: 1.6,
	Jitter:     0.2,
	MaxDelay:   8 * time.Second,
}

// Client manages the gRPC channel and RPC calls to the Rust engine.
//
// The channel is created lazily by the first RPC and shared for the lifetime of
// the Client. gRPC reconnects automatically when the engine restarts, and each
// connection-level failure resets its backoff so recovery is prompt. Concurrent
// RPCs share one channel; the mutex guards channel creation, Close, and the
// closed flag so a closed client never dials again.
type Client struct {
	address   string
	retry     RetryConfig
	logger    *slog.Logger
	metrics   *metrics.Registry
	dial      func(ctx context.Context, address string) (*grpc.ClientConn, error)
	mu        sync.Mutex
	conn      *grpc.ClientConn
	transport Transport
	closed    bool
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

// defaultDialer builds a non-blocking channel. Dialing does not block or
// contact the engine; gRPC connects in the background, so an engine that starts
// after the proxy (or restarts) is picked up without the proxy exiting.
func defaultDialer(_ context.Context, address string) (*grpc.ClientConn, error) {
	return grpc.NewClient(address,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithConnectParams(grpc.ConnectParams{Backoff: reconnectBackoff}),
	)
}

// NewClient constructs a client for the given engine address. It does not
// connect until the first RPC, so the engine does not need to be ready when the
// proxy starts.
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
		dial:    defaultDialer,
	}
}

// NewClientWithTransport constructs a client with an injected transport. It
// disables lazy dialing so the provided transport is used unmodified; the
// missing-transport path then yields ErrEngineTransportNotConfigured on RPCs.
func NewClientWithTransport(address string, retry RetryConfig, transport Transport, logger *slog.Logger) *Client {
	c := NewClient(address, retry, logger)
	c.transport = transport
	c.dial = nil
	return c
}

// Close closes the underlying gRPC channel if one was created and marks the
// client closed. It is idempotent and safe to call on a client whose connection
// was never established. RPCs after Close fail with ErrEngineClientClosed
// instead of silently opening a fresh connection.
func (c *Client) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closed = true
	if c.conn == nil {
		return nil
	}
	err := c.conn.Close()
	c.conn = nil
	c.transport = nil
	return err
}

// getTransport returns the transport for an RPC, creating the channel lazily on
// first use. After the initial creation the same channel is reused, and gRPC
// reconnects it on demand when the engine is temporarily unreachable. A closed
// client does not dial again: it fails with ErrEngineClientClosed.
func (c *Client) getTransport(ctx context.Context) (Transport, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil, protocol.ErrEngineClientClosed
	}
	if c.transport != nil {
		return c.transport, nil
	}
	if c.dial == nil {
		return nil, protocol.ErrEngineTransportNotConfigured
	}
	if c.address == "" {
		return nil, errors.New("engine address is empty")
	}
	conn, err := c.dial(ctx, c.address)
	if err != nil {
		return nil, err
	}
	c.conn = conn
	c.transport = NewGRPCTransport(conn)
	return c.transport, nil
}

// nudgeReconnect resets the gRPC backoff so a channel that lost its engine
// reconnect begins dialing again promptly instead of waiting out its exponential
// backoff. It is a no-op when no channel exists yet.
func (c *Client) nudgeReconnect() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn != nil {
		c.conn.ResetConnectBackoff()
	}
}

// Intercept asks the engine how the proxy should route one tool call.
func (c *Client) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	start := time.Now()
	resp, err := call(ctx, c, func(t Transport) (protocol.InterceptResponse, error) {
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
	resp, err := call(ctx, c, func(t Transport) (protocol.ApproveResponse, error) {
		return t.Approve(ctx, req)
	})
	c.observeRPC(rpcMethodApprove, start, err)
	return resp, err
}

// Reject rejects an approval request in the engine.
func (c *Client) Reject(ctx context.Context, req protocol.RejectRequest) error {
	start := time.Now()
	err := c.callVoid(ctx, func(t Transport) error {
		return t.Reject(ctx, req)
	})
	c.observeRPC(rpcMethodReject, start, err)
	return err
}

// ListPendingApprovals returns the engine's unresolved approvals for one organization.
func (c *Client) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	start := time.Now()
	resp, err := call(ctx, c, func(t Transport) (protocol.ListPendingApprovalsResponse, error) {
		return t.ListPendingApprovals(ctx, req)
	})
	c.observeRPC(rpcMethodListPending, start, err)
	return resp, err
}

// call obtains the transport lazily, then delegates to fn. Generic over any
// return type T. Used by Intercept, Approve, and ListPendingApprovals.
func call[T any](ctx context.Context, c *Client, fn func(Transport) (T, error)) (T, error) {
	t, err := c.getTransport(ctx)
	if err != nil {
		var zero T
		return zero, err
	}
	resp, err := fn(t)
	if isConnectionLoss(err) {
		c.nudgeReconnect()
	}
	return resp, err
}

func (c *Client) callVoid(ctx context.Context, fn func(Transport) error) error {
	t, err := c.getTransport(ctx)
	if err != nil {
		return err
	}
	rpcErr := fn(t)
	if isConnectionLoss(rpcErr) {
		c.nudgeReconnect()
	}
	return rpcErr
}

// retryVoid invokes fn and retries transient transport failures with bounded
// backoff. Only Commit and Fail use it: the engine's state-machine transitions
// make both idempotent, so a retried call cannot double-apply. Intercept,
// Approve, Reject, and ListPendingApprovals are intentionally not retried.
// A connection-level failure also resets the channel backoff so the engine is
// reconnected promptly after the retry budget is exhausted.
func (c *Client) retryVoid(ctx context.Context, method string, fn func(Transport) error) error {
	t, err := c.getTransport(ctx)
	if err != nil {
		return err
	}
	var last error
	for attempt := 1; attempt <= c.retry.MaxAttempts; attempt++ {
		last = fn(t)
		if last == nil {
			return nil
		}
		if isConnectionLoss(last) {
			c.nudgeReconnect()
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

// isConnectionLoss reports whether the error came from the transport rather than
// the engine's application logic. Unavailable covers a down or restarting
// engine; Unknown covers connection-level failures gRPC surfaces without a
// specific status. On these the client resets the channel backoff so the engine
// reconnects promptly.
func isConnectionLoss(err error) bool {
	switch status.Code(err) {
	case codes.Unavailable, codes.Unknown:
		return true
	default:
		return false
	}
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
