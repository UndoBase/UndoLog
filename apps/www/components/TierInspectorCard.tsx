/**
 * TierInspectorCard: Floating overlay card explaining the three tool tiers.
 * Positioned absolutely over DecoratorShowcase. Shows SAFE, COMPENSABLE,
 * and IRREVERSIBLE tiers with color coding and descriptions.
 */
import { Shield } from "lucide-react";
import { FloatCard } from "./Frame";

const tiers = [
  { label: "SAFE", color: "#1D9E75", desc: "No effect log. Auto-replayed on cache hit." },
  { label: "COMPENSABLE", color: "#EF9F27", desc: "Pre-registers compensation. Logged to journal. Saga LIFO on failure." },
  { label: "IRREVERSIBLE", color: "#D85A30", desc: "Approval gate. Session suspended until human judgment." },
];

export default function TierInspectorCard() {
  return (
    <FloatCard
      style={{
        position: "absolute",
        right: -8,
        top: "50%",
        transform: "translateY(-50%)",
        width: 232,
        padding: 14,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
          paddingBottom: 10,
          borderBottom: "1px solid rgba(255,255,255,0.05)",
        }}
      >
        <Shield size={14} color="var(--purple-primary)" />
        <span
          style={{
            fontFamily: "'Geist', sans-serif",
            fontSize: 11.5,
            fontWeight: 500,
            color: "rgba(255,255,255,0.9)",
          }}
        >
          Tier Inspector
        </span>
      </div>
      {tiers.map((t) => (
        <div
          key={t.label}
          style={{
            display: "flex",
            gap: 8,
            padding: "6px 0",
            borderBottom: "1px solid rgba(255,255,255,0.04)",
          }}
        >
          <span
            style={{
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: t.color,
              marginTop: 4,
              flexShrink: 0,
            }}
          />
          <div>
            <div
              style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 9.5,
                fontWeight: 500,
                color: t.color,
                letterSpacing: "0.06em",
                marginBottom: 1,
              }}
            >
              {t.label}
            </div>
            <div
              style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 9,
                color: "rgba(255,255,255,0.25)",
                lineHeight: 1.4,
              }}
            >
              {t.desc}
            </div>
          </div>
        </div>
      ))}
    </FloatCard>
  );
}
