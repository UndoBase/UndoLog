// Package engine provides the gRPC transport that connects the proxy to the Rust engine.
//
// This file implements the “Transport“ interface using the generated proto stubs
// from “proto/undolog.proto“ (via “protoc-gen-go-grpc“).
package engine

import (
	"context"
	"encoding/json"

	"google.golang.org/grpc"

	"undolog-proxy/internal/engine/pb"
	"undolog-proxy/internal/protocol"
)

// GRPCTransport adapts the generated “pb.UndoLogEngineClient“ to the
// “protocol.EngineClient“ interface expected by the proxy handler.
//
// Usage:
//
//	conn, _ := grpc.Dial(address, grpc.WithTransportCredentials(insecure.NewCredentials()))
//	transport := NewGRPCTransport(conn)
//	client := NewClientWithTransport(address, retryConfig, transport, logger)
type GRPCTransport struct {
	client pb.UndoLogEngineClient
}

// NewGRPCTransport creates a transport backed by a gRPC connection to the Rust engine.
func NewGRPCTransport(cc grpc.ClientConnInterface) *GRPCTransport {
	return &GRPCTransport{
		client: pb.NewUndoLogEngineClient(cc),
	}
}

// Intercept sends the tool call to the Rust engine and returns the routing decision.
func (t *GRPCTransport) Intercept(ctx context.Context, req protocol.InterceptRequest) (protocol.InterceptResponse, error) {
	pbReq := &pb.InterceptRequest{
		ToolCall: toolCallToProto(req.ToolCall),
	}

	pbResp, err := t.client.Intercept(ctx, pbReq)
	if err != nil {
		return protocol.InterceptResponse{}, err
	}

	return interceptResponseFromProto(pbResp), nil
}

// Commit reports a successful execution to the engine.
func (t *GRPCTransport) Commit(ctx context.Context, req protocol.CommitRequest) error {
	pbReq := commitRequestToProto(req)
	_, err := t.client.Commit(ctx, pbReq)
	return err
}

// Fail reports a failed execution to the engine.
func (t *GRPCTransport) Fail(ctx context.Context, req protocol.FailRequest) error {
	pbReq := failRequestToProto(req)
	_, err := t.client.Fail(ctx, pbReq)
	return err
}

// Approve resumes a pending approval request and returns execution data.
func (t *GRPCTransport) Approve(ctx context.Context, req protocol.ApproveRequest) (protocol.ApproveResponse, error) {
	pbReq := approveRequestToProto(req)
	pbResp, err := t.client.Approve(ctx, pbReq)
	if err != nil {
		return protocol.ApproveResponse{}, err
	}
	return approveResponseFromProto(pbResp), nil
}

// Reject rejects a pending approval request.
func (t *GRPCTransport) Reject(ctx context.Context, req protocol.RejectRequest) error {
	pbReq := rejectRequestToProto(req)
	_, err := t.client.Reject(ctx, pbReq)
	return err
}

// Close is a no-op. The caller owns the gRPC connection lifecycle.
func (t *GRPCTransport) Close() error { return nil }

// ── Proto ↔ Protocol conversions ──────────────────────────────────────────

func toolCallToProto(call protocol.ToolCall) *pb.ToolCall {
	argsRaw, _ := json.Marshal(call.Args)
	return &pb.ToolCall{
		OrgId:       call.OrgID,
		SessionId:   call.SessionID,
		ToolId:      call.ToolID,
		ToolName:    call.ToolName,
		ToolVersion: call.ToolVersion,
		StepIndex:   call.StepIndex,
		Args:        argsRaw,
		Signature:   call.Signature,
	}
}

func toolResultToProto(result protocol.ToolResult) *pb.ToolResult {
	return &pb.ToolResult{
		Success:    result.Success,
		Output:     result.Output,
		Error:      result.Error,
		DurationMs: result.DurationMS,
	}
}

func toolResultFromProto(msg *pb.ToolResult) protocol.ToolResult {
	return protocol.ToolResult{
		Success:    msg.Success,
		Output:     msg.Output,
		Error:      msg.Error,
		DurationMS: msg.DurationMs,
	}
}

func interceptResponseFromProto(msg *pb.InterceptResponse) protocol.InterceptResponse {
	resp := protocol.InterceptResponse{}

	if ex := msg.GetExecute(); ex != nil {
		resp.Outcome = protocol.InterceptExecute
		resp.EffectID = ex.EffectId
	} else if rp := msg.GetReplay(); rp != nil {
		resp.Outcome = protocol.InterceptReplay
		resp.EffectID = rp.EffectId
		if rp.CachedResult != nil {
			r := toolResultFromProto(rp.CachedResult)
			resp.CachedResult = &r
		}
	} else if ap := msg.GetAwaitingApproval(); ap != nil {
		resp.Outcome = protocol.InterceptAwaitingApproval
		resp.ApprovalID = ap.ApprovalId
	}

	return resp
}

func commitRequestToProto(req protocol.CommitRequest) *pb.CommitRequest {
	return &pb.CommitRequest{
		OrgId:     req.OrgID,
		SessionId: req.SessionID,
		EffectId:  req.EffectID,
		Result:    toolResultToProto(req.Result),
	}
}

func failRequestToProto(req protocol.FailRequest) *pb.FailRequest {
	return &pb.FailRequest{
		OrgId:     req.OrgID,
		SessionId: req.SessionID,
		EffectId:  req.EffectID,
		Error:     req.Error,
	}
}

func approveResponseFromProto(msg *pb.ApproveResponse) protocol.ApproveResponse {
	return protocol.ApproveResponse{
		EffectID:    msg.EffectId,
		SessionID:   msg.SessionId,
		ToolName:    msg.ToolName,
		ToolVersion: msg.ToolVersion,
		Args:        msg.Args,
	}
}

func approveRequestToProto(req protocol.ApproveRequest) *pb.ApproveRequest {
	return &pb.ApproveRequest{
		OrgId:        req.OrgID,
		ApprovalId:   req.ApprovalID,
		Actor:        req.Actor,
		ApprovedArgs: req.ApprovedArgs,
	}
}

func rejectRequestToProto(req protocol.RejectRequest) *pb.RejectRequest {
	return &pb.RejectRequest{
		OrgId:      req.OrgID,
		ApprovalId: req.ApprovalID,
	}
}
