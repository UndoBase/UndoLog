/**
 * AgentRunShowcase: Terminal-style execution log showing real-time
 * tool call classification with per-tier color coding.
 * Includes a floating Effect Journal overlay showing committed/pending effects.
 */
import { Frame } from "./Frame";
import EffectJournalCard from "./EffectJournalCard";

export default function AgentRunShowcase() {
  return (
    <div style={{ position: "relative" }}>
      <Frame noTitle>
        <div
          style={{
            padding: "16px 20px",
            fontFamily: "'Geist Mono','JetBrains Mono',monospace",
            fontSize: 12,
            lineHeight: "20px",
            background: "var(--bg-elevated)",
            height: 520,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div style={{ flex: 1, overflow: "auto" }}>
            <div
              style={{
                display: "flex",
                gap: 6,
                color: "rgba(255,255,255,0.15)",
                fontSize: 11,
              }}
            >
              <span style={{ color: "var(--purple-primary)" }}>undolog</span>
              <span style={{ color: "rgba(255,255,255,0.1)" }}>~/agent</span>
              <span style={{ color: "rgba(255,255,255,0.35)" }}>$</span>
              <span>python agent.py --customer alice --ticket high</span>
            </div>

            <div style={{ marginTop: 14 }}>
              <span style={{ color: "#1D9E75" }}>● SAFE</span>{" "}
              <span style={{ color: "var(--text-code)" }}>lookup_customer</span>
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              ✓ replayed from cache (call_signature: a1b2…3e4f)
            </div>
            <div style={{ paddingLeft: 20, color: "#1D9E75", fontSize: 11 }}>
              → name: Alice Chen · plan: enterprise · balance: $249
            </div>

            <div style={{ marginTop: 8 }}>
              <span style={{ color: "#1D9E75" }}>● SAFE</span>{" "}
              <span style={{ color: "var(--text-code)" }}>search_knowledge_base</span>
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              ✓ 2 results (KB#412: Enterprise duplicate charge workflow)
            </div>

            <div style={{ marginTop: 8 }}>
              <span style={{ color: "#EF9F27" }}>● COMP.</span>{" "}
              <span style={{ color: "var(--text-code)" }}>create_ticket</span>
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              ⤵ compensation: undo_create_ticket registered
            </div>
            <div style={{ paddingLeft: 20, color: "#EF9F27", fontSize: 11 }}>
              → TKT-4421 · status: draft · refund: $249
            </div>

            <div style={{ marginTop: 8 }}>
              <span style={{ color: "#D85A30" }}>● IRREV.</span>{" "}
              <span style={{ color: "var(--text-code)" }}>send_email</span>
            </div>
            <div style={{ paddingLeft: 20, color: "#D85A30", fontSize: 11 }}>
              ⚡ approval_id: apr_9f8e, session suspended
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              waiting for human decision...
            </div>
          </div>

          <div
            style={{
              marginTop: 16,
              paddingTop: 10,
              borderTop: "1px solid rgba(255,255,255,0.04)",
              color: "var(--text-muted)",
              fontSize: 10.5,
              flexShrink: 0,
            }}
          >
            session: a1b2c3d4 · 4 tool calls ·{" "}
            <span style={{ color: "#1D9E75" }}>2 SAFE</span> ·{" "}
            <span style={{ color: "#EF9F27" }}>1 COMP.</span> ·{" "}
            <span style={{ color: "#D85A30" }}>1 pending approval</span>
          </div>
        </div>
      </Frame>

      <EffectJournalCard />
    </div>
  );
}
