/**
 * EventStreamPanel: Live SSE-style event log for the approval workflow.
 * Displays session lifecycle events (effect_committed, approval_required,
 * session_suspended/resumed/terminated) with per-event color coding.
 * The `resolution` prop drives the terminal event display.
 */
import { Check, Zap, Play } from "lucide-react";
import { Frame } from "./Frame";

export default function EventStreamPanel({
  resolution,
}: {
  resolution: "pending" | "approved" | "rejected";
}) {
  return (
    <Frame
      noTitle
      style={{
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: 16,
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
          flex: 1,
        }}
      >
        <div
          style={{
            paddingBottom: 10,
            borderBottom: "1px solid var(--border-primary)",
            marginBottom: 14,
          }}
        >
          <div
            style={{
              fontFamily: "'Geist', sans-serif",
              fontSize: 11,
              fontWeight: 500,
              color: "var(--text-code)",
              marginBottom: 2,
            }}
          >
            Event Stream
          </div>
          <div
            style={{
              fontFamily: "'Geist Mono', monospace",
              fontSize: 9,
              color: "var(--text-muted)",
              letterSpacing: "0.08em",
            }}
          >
            SSE · LIVE
          </div>
        </div>

        <div
          style={{
            fontFamily: "'Geist Mono', monospace",
            fontSize: 10,
            lineHeight: 2.2,
            flex: 1,
          }}
        >
          <div>
            <span style={{ color: "#1D9E75" }}>
              event: effect_committed
            </span>
          </div>
          <div style={{ color: "rgba(255,255,255,0.2)" }}>
            event: compensation_registered
          </div>
          <div style={{ color: "#D85A30" }}>
            event: approval_required
            {resolution === "approved"
              ? ": resolved: approved"
              : resolution === "rejected"
                ? ": resolved: rejected"
                : ""}{" "}
            <span
              style={{
                color: "rgba(255,255,255,0.12)",
                marginLeft: 4,
              }}
            >
              apr_9f8e
            </span>
          </div>
          <div
            style={{
              color:
                resolution === "approved"
                  ? "#1D9E75"
                  : resolution === "rejected"
                    ? "#D85A30"
                    : "rgba(255,255,255,0.2)",
            }}
          >
            {resolution === "pending"
              ? "event: session_suspended"
              : resolution === "approved"
                ? "event: session_resumed"
                : "event: session_terminated"}
          </div>
          <div
            style={{
              marginTop: 8,
              paddingTop: 8,
              borderTop: "1px solid rgba(255,255,255,0.04)",
              color: "rgba(255,255,255,0.1)",
            }}
          >
            ...
          </div>
        </div>

        <div
          style={{
            marginTop: 8,
            paddingTop: 10,
            borderTop: "1px solid rgba(255,255,255,0.04)",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9,
            color: "rgba(255,255,255,0.15)",
            lineHeight: 1.8,
          }}
        >
          <div
            style={{
              display: "flex",
              gap: 12,
              alignItems: "center",
            }}
          >
            <span
              style={{
                color: "#1D9E75",
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
              }}
            >
              <Check size={10} /> effect_intercepted
            </span>
            <span
              style={{
                color: "#1D9E75",
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
              }}
            >
              <Check size={10} /> compensation_registered
            </span>
          </div>
          <div
            style={{
              display: "flex",
              gap: 12,
              alignItems: "center",
            }}
          >
            <span
              style={{
                color:
                  resolution === "approved" ? "#1D9E75" : "#D85A30",
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
              }}
            >
              {resolution === "approved" ? (
                <Check size={10} />
              ) : (
                <Zap size={10} />
              )}
              {resolution === "approved"
                ? "approval_granted"
                : resolution === "rejected"
                  ? "approval_denied"
                  : "approval_required"}
            </span>
            <span
              style={{
                color:
                  resolution === "approved" ? "#1D9E75" : "#D85A30",
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
              }}
            >
              {resolution === "approved" ? (
                <Play size={10} />
              ) : (
                <Zap size={10} />
              )}
              {resolution === "approved"
                ? "session_resumed"
                : resolution === "rejected"
                  ? "session_terminated"
                  : "session_suspended"}
            </span>
          </div>
        </div>
      </div>
    </Frame>
  );
}
