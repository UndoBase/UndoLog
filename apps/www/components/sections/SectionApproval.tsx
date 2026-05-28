import { useState } from "react";
import {
  ShieldAlert,
  Check,
  X,
} from "lucide-react";
import { Frame } from "../Frame";
import { ST } from "../Syntax";
import { C, SEC, H2, BODY } from "./shared";
import ArrowIcon from "../ArrowIcon";
import EventStreamPanel from "../EventStreamPanel";

/**
 * SectionApproval: Human-in-the-loop approval demo (FIG 1.2).
 * Interactive approve/reject workflow with risk tags, args display,
 * and a real-time Event Stream panel showing session lifecycle events.
 */
export default function SectionApproval() {
  const [resolution, setResolution] = useState<
    "pending" | "approved" | "rejected"
  >("pending");

  return (
    <section style={{ ...SEC }}>
      <div style={C}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 0.9fr",
            gap: 48,
            marginBottom: 56,
            alignItems: "start",
          }}
        >
          <div>
            <h2 style={H2}>When an action is irreversible, only a human can grant approval.</h2>
          </div>
          <div style={{ paddingLeft: 24 }}>
            <p style={BODY}>
              The session suspends the moment an irreversible action
              is requested. Risk tags, full context, and a single click:
              approved or rejected.
            </p>
            <div style={{ marginTop: 16 }}>
              <a href="/docs/guides/configuring-approval-gates" className="ghost-link">Learn about approvals<ArrowIcon /></a>
            </div>
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 0.8fr",
            gap: 20,
            alignItems: "stretch",
            height: 520,
          }}
        >
          <Frame
            noTitle
            style={{
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div style={{ flex: 1, overflowY: "auto", padding: "20px 20px 0" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  marginBottom: 20,
                  paddingBottom: 14,
                  borderBottom: "1px solid var(--border-primary)",
                }}
              >
                <ShieldAlert
                  size={16}
                  color={
                    resolution === "rejected"
                      ? "var(--text-muted)"
                      : "#D85A30"
                  }
                />
                <div>
                  <div
                    style={{
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 9,
                      color: "var(--text-muted)",
                      letterSpacing: "0.12em",
                      marginBottom: 1,
                      textTransform: "uppercase",
                    }}
                  >
                    ACTION REQUIRED
                  </div>
                  <div
                    style={{
                      fontFamily: "'Geist', sans-serif",
                      fontSize: 14,
                      fontWeight: 500,
                      color: "#FFFFFF",
                    }}
                  >
                    Approval Request
                  </div>
                </div>
                <div
                  style={{
                    marginLeft: "auto",
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background:
                        resolution === "approved"
                          ? "#1D9E75"
                          : resolution === "rejected"
                            ? "var(--text-muted)"
                            : "#D85A30",
                      boxShadow:
                        resolution === "pending"
                          ? "0 0 6px #D85A30"
                          : "none",
                    }}
                  />
                  <span
                    style={{
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 9,
                      color:
                        resolution === "approved"
                          ? "#1D9E75"
                          : resolution === "rejected"
                            ? "var(--text-muted)"
                            : "#D85A30",
                      letterSpacing: "0.06em",
                    }}
                  >
                    {resolution === "approved"
                      ? "APPROVED"
                      : resolution === "rejected"
                        ? "REJECTED"
                        : "PENDING"}
                  </span>
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: 14,
                  padding: "9px 12px",
                  borderRadius: "var(--radius-sm)",
                  background: "var(--bg-header)",
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: 9,
                      color: "rgba(255,255,255,0.15)",
                      fontFamily: "'Geist Mono', monospace",
                      marginBottom: 2,
                      letterSpacing: "0.08em",
                    }}
                  >
                    TOOL
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: "rgba(255,255,255,0.85)",
                      fontFamily: "'Geist Mono', monospace",
                    }}
                  >
                    send_email
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div
                    style={{
                      fontSize: 9,
                      color: "rgba(255,255,255,0.15)",
                      fontFamily: "'Geist Mono', monospace",
                      marginBottom: 2,
                      letterSpacing: "0.08em",
                    }}
                  >
                    SESSION
                  </div>
                  <div
                    style={{
                      fontSize: 10,
                      color: "rgba(255,255,255,0.25)",
                      fontFamily: "'Geist Mono', monospace",
                    }}
                  >
                    a1b2c3d4-e5f6
                  </div>
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  gap: 4,
                  marginBottom: 14,
                  flexWrap: "wrap",
                }}
              >
                {[
                  "external_communication",
                  "financial_data",
                  "customer_facing",
                ].map((t) => (
                  <span
                    key={t}
                    style={{
                      padding: "2px 7px",
                      borderRadius: 3,
                      fontSize: 9,
                      fontFamily: "'Geist Mono', monospace",
                      color:
                        resolution === "rejected"
                          ? "var(--text-muted)"
                          : "#D85A30",
                      background:
                        resolution === "rejected"
                          ? "rgba(255,255,255,0.03)"
                          : "rgba(216,90,48,0.06)",
                    }}
                  >
                    {t}
                  </span>
                ))}
              </div>

              <div
                style={{
                  background: "var(--bg-header)",
                  borderRadius: "var(--radius-sm)",
                  padding: 10,
                  marginBottom: 18,
                  fontFamily: "'Geist Mono', monospace",
                  fontSize: 11,
                  lineHeight: 1.8,
                }}
              >
                <div
                  style={{
                    color: "var(--text-muted)",
                    marginBottom: 3,
                    fontSize: 9,
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                  }}
                >
                  ARGS
                </div>
                <div style={{ color: "rgba(255,255,255,0.55)" }}>{"{"}</div>
                <div
                  style={{
                    paddingLeft: 12,
                    color: "var(--text-code)",
                  }}
                >
                  to: <ST>"alice@example.com"</ST>,
                </div>
                <div
                  style={{
                    paddingLeft: 12,
                    color: "var(--text-code)",
                  }}
                >
                  subject: <ST>"Refund processing"</ST>,
                </div>
                <div
                  style={{
                    paddingLeft: 12,
                    color: "var(--text-code)",
                  }}
                >
                  body:{" "}
                  <ST>"Hi Alice, we're processing your $249 refund..."</ST>
                </div>
                <div style={{ color: "rgba(255,255,255,0.55)" }}>{"}"}</div>
              </div>
            </div>

            <div style={{ padding: "0 20px 20px" }}>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={() => setResolution("approved")}
                  disabled={resolution !== "pending"}
                  style={{
                    flex: 1,
                    padding: "8px 0",
                    borderRadius: "var(--radius-sm)",
                    border: "none",
                    background:
                      resolution === "approved"
                        ? "rgba(29,158,117,0.12)"
                        : resolution === "pending"
                          ? "#1D9E75"
                          : "rgba(255,255,255,0.05)",
                    color:
                      resolution === "approved"
                        ? "#1D9E75"
                        : resolution === "pending"
                          ? "#fff"
                          : "rgba(255,255,255,0.2)",
                    fontWeight: 500,
                    fontFamily: "'Geist', sans-serif",
                    fontSize: 13,
                    cursor:
                      resolution === "pending" ? "pointer" : "default",
                    transition: "all 200ms ease",
                  }}
                >
                  {resolution === "approved"
                    ? "✓ Approved"
                    : resolution === "rejected"
                      ? "Approve"
                      : "Approve →"}
                </button>
                <button
                  onClick={() => setResolution("rejected")}
                  disabled={resolution !== "pending"}
                  style={{
                    flex: 1,
                    padding: "8px 0",
                    borderRadius: "var(--radius-sm)",
                    border:
                      resolution === "rejected"
                        ? "1px solid rgba(216,90,48,0.3)"
                        : "1px solid rgba(216,90,48,0.2)",
                    background:
                      resolution === "rejected"
                        ? "rgba(216,90,48,0.08)"
                        : "transparent",
                    color:
                      resolution === "rejected"
                        ? "#D85A30"
                        : resolution === "pending"
                          ? "#D85A30"
                          : "rgba(255,255,255,0.2)",
                    fontWeight: 500,
                    fontFamily: "'Geist', sans-serif",
                    fontSize: 13,
                    cursor:
                      resolution === "pending" ? "pointer" : "default",
                    transition: "all 200ms ease",
                  }}
                >
                  {resolution === "rejected" ? "✕ Rejected" : "Reject"}
                </button>
              </div>

              {resolution === "approved" && (
                <div
                  style={{
                    marginTop: 14,
                    padding: "8px 12px",
                    borderRadius: "var(--radius-sm)",
                    background: "rgba(29,158,117,0.06)",
                    border: "1px solid rgba(29,158,117,0.1)",
                    fontFamily: "'Geist Mono', monospace",
                    fontSize: 10,
                    color: "#1D9E75",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <Check size={14} color="#1D9E75" />
                  <span>
                    Action approved, resuming session (result replayed from
                    cache)
                  </span>
                </div>
              )}
              {resolution === "rejected" && (
                <div
                  style={{
                    marginTop: 14,
                    padding: "8px 12px",
                    borderRadius: "var(--radius-sm)",
                    background: "rgba(216,90,48,0.06)",
                    border: "1px solid rgba(216,90,48,0.1)",
                    fontFamily: "'Geist Mono', monospace",
                    fontSize: 10,
                    color: "#D85A30",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <X size={14} color="#D85A30" />
                  <span>
                    Action rejected; session terminated (compensation rolling
                    back)
                  </span>
                </div>
              )}
            </div>
          </Frame>

          <EventStreamPanel resolution={resolution} />
        </div>
      </div>
    </section>
  );
}
