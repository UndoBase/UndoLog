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
	"google.golang.org/grpc/credentials/insecure"

	"undolog-proxy/internal/protocol"
)

// RetryConfig controls engine connection retry behavior.
type RetryConfig struct {
	// MaxAttempts is the maximum number of connection attempts.
	MaxAttempts int
	// Backoff is the base delay between retries.
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
}

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
	return call(c, func(t Transport) (protocol.InterceptResponse, error) {
		return t.Intercept(ctx, req)
	})
}

// Commit reports a successful execution to the engine.
func (c *Client) Commit(ctx context.Context, req protocol.CommitRequest) error {
	return c.callVoid(func(t Transport) error {
		return t.Commit(ctx, req)
	})
}

// Fail reports a failed execution to the engine.
func (c *Client) Fail(ctx context.Context, req protocol.FailRequest) error {
	return c.callVoid(func(t Transport) error {
		return t.Fail(ctx, req)
	})
}

// Approve resumes an approval request in the engine and returns execution data.
func (c *Client) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	return call(c, func(t Transport) (protocol.ApproveResponse, error) {
		return t.Approve(ctx, req)
	})
}

// Reject rejects an approval request in the engine.
func (c *Client) Reject(ctx context.Context, req protocol.RejectRequest) error {
	return c.callVoid(func(t Transport) error {
		return t.Reject(ctx, req)
	})
}

// ListPendingApprovals returns the engine's unresolved approvals for one org.
func (c *Client) ListPendingApprovals(ctx context.Context, req protocol.ListPendingApprovalsRequest) (protocol.ListPendingApprovalsResponse, error) {
	return call(c, func(t Transport) (protocol.ListPendingApprovalsResponse, error) {
		return t.ListPendingApprovals(ctx, req)
	})
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
