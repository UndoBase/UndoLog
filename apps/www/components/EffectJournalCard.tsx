/**
 * EffectJournalCard: Floating overlay card showing the effect journal state.
 * Positioned absolutely over AgentRunShowcase. Lists committed and pending
 * effects with tier color coding and a status indicator.
 */
import { ClipboardList } from "lucide-react";
import { FloatCard } from "./Frame";

const effects = [
  { id: "eff_a1b2c3d4", tool: "create_ticket", tier: "COMP.", tc: "#EF9F27", state: "committed" },
  { id: "eff_5e6f7a8b", tool: "send_email", tier: "IRREV.", tc: "#D85A30", state: "pending" },
];

export default function EffectJournalCard() {
  return (
    <FloatCard
      style={{
        position: "absolute",
        right: -8,
        top: 20,
        width: 215,
        padding: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 10,
          paddingBottom: 8,
          borderBottom: "1px solid rgba(255,255,255,0.05)",
        }}
      >
        <ClipboardList size={13} style={{ opacity: 0.7 }} />
        <span
          style={{
            fontFamily: "'Geist', sans-serif",
            fontSize: 11,
            fontWeight: 500,
            color: "rgba(255,255,255,0.85)",
          }}
        >
          Effect Journal
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "'Geist Mono', monospace",
            fontSize: 9,
            color: "rgba(255,255,255,0.15)",
            background: "rgba(255,255,255,0.03)",
            padding: "1px 6px",
            borderRadius: 3,
          }}
        >
          LIVE
        </span>
      </div>
      {effects.map((e) => (
        <div
          key={e.id}
          style={{
            padding: "5px 0",
            borderBottom: "1px solid rgba(255,255,255,0.03)",
            fontSize: 9.5,
            fontFamily: "'Geist Mono', monospace",
            lineHeight: 1.6,
          }}
        >
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <span style={{ color: "rgba(255,255,255,0.2)", fontSize: 9 }}>{e.id}</span>
            <span
              style={{
                marginLeft: "auto",
                fontSize: 9,
                padding: "1px 5px",
                borderRadius: 2,
                background: `${e.tc}12`,
                color: e.tc,
              }}
            >
              {e.tier}
            </span>
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 1 }}>
            <span style={{ color: "rgba(255,255,255,0.4)", fontSize: 9.5 }}>{e.tool}</span>
            <span
              style={{
                marginLeft: "auto",
                color: e.state === "committed" ? "#1D9E75" : "#D85A30",
                fontSize: 9,
              }}
            >
              {e.state}
            </span>
          </div>
        </div>
      ))}
      <div
        style={{
          marginTop: 6,
          fontFamily: "'Geist Mono', monospace",
          fontSize: 8.5,
          color: "rgba(255,255,255,0.12)",
          textAlign: "center",
        }}
      >
        append-only · BLAKE3 deduplicated
      </div>
    </FloatCard>
  );
}
