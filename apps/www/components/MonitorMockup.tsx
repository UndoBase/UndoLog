import { Menu, Layers, Zap, Play, Check } from "lucide-react";
import { Frame } from "./Frame";

/**
 * MonitorMockup: 3-panel agent session monitor.
 * Left sidebar with session list and tier badges, center terminal output,
 * right metadata panel. Used in the product page hero.
 */
export default function MonitorMockup() {
  return (
    <div style={{ position: "relative" }}>
      <Frame noTitle>
        <div style={{ display: "flex", height: 520 }}>
          <div
            style={{
              width: "22%",
              borderRight: "1px solid var(--border-subtle)",
              padding: "16px 12px",
              background: "var(--bg-elevated)",
              overflowY: "auto",
            }}
          >
            <div style={{ marginBottom: 18 }}>
              <div
                style={{
                  fontFamily: "'Geist', sans-serif",
                  fontSize: 11,
                  fontWeight: 500,
                  color: "#FFFFFF",
                  marginBottom: 8,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Menu size={12} style={{ opacity: 0.4 }} /> Sessions
                <span
                  style={{
                    fontFamily: "'Geist Mono', monospace",
                    fontSize: 10,
                    color: "rgba(255,255,255,0.2)",
                    fontWeight: 400,
                    marginLeft: "auto",
                  }}
                >
                  12
                </span>
              </div>
              {[
                { id: "a1b2c3d4", status: "⚡", sc: "#D85A30", time: "2m ago", active: true },
                { id: "5e6f7a8b", status: "▶", sc: "#1D9E75", time: "5m ago", active: false },
                { id: "9c0d1e2f", status: "✓", sc: "rgba(255,255,255,0.15)", time: "12m ago", active: false },
                { id: "3a4b5c6d", status: "✓", sc: "rgba(255,255,255,0.15)", time: "18m ago", active: false },
              ].map((s) => (
                <div
                  key={s.id}
                  className={s.active ? "hero-mockup-pulse" : undefined}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "6px 8px",
                    borderRadius: "var(--radius-sm)",
                    marginBottom: 1,
                    height: 30,
                    background: s.active ? "var(--bg-active)" : "transparent",
                    border: s.active ? "1px solid rgba(255,255,255,0.06)" : "1px solid transparent",
                  }}
                >
                  {s.status === "⚡" ? (
                    <Zap size={9} color={s.sc} style={{ flexShrink: 0 }} />
                  ) : s.status === "▶" ? (
                    <Play size={9} color={s.sc} style={{ flexShrink: 0 }} />
                  ) : (
                    <Check size={9} color={s.sc} style={{ flexShrink: 0 }} />
                  )}
                  <span
                    style={{
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 10.5,
                      color: s.active ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.35)",
                    }}
                  >
                    {s.id}
                  </span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontFamily: "'Geist Mono', monospace",
                      fontSize: 9,
                      color: "rgba(255,255,255,0.12)",
                    }}
                  >
                    {s.time}
                  </span>
                </div>
              ))}
            </div>

            <div style={{ height: 1, background: "var(--border-subtle)", marginBottom: 14 }} />

            <div
              style={{
                fontFamily: "'Geist', sans-serif",
                fontSize: 10.5,
                fontWeight: 500,
                color: "#FFFFFF",
                marginBottom: 8,
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Layers size={12} style={{ opacity: 0.4 }} /> Tiers
            </div>
            {["SAFE", "COMPENSABLE", "IRREVERSIBLE"].map((t) => (
              <div
                key={t}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "5px 8px",
                  fontFamily: "'Geist Mono', monospace",
                  fontSize: 10.5,
                  color: t === "SAFE" ? "#1D9E75" : t === "COMPENSABLE" ? "#EF9F27" : "#D85A30",
                }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: t === "SAFE" ? "#1D9E75" : t === "COMPENSABLE" ? "#EF9F27" : "#D85A30",
                    boxShadow: `0 0 4px ${t === "SAFE" ? "#1D9E75" : t === "COMPENSABLE" ? "#EF9F27" : "#D85A30"}`,
                    flexShrink: 0,
                  }}
                />
                {t}
                <span
                  style={{
                    marginLeft: "auto",
                    fontSize: 10,
                    color: `${t === "SAFE" ? "#1D9E75" : t === "COMPENSABLE" ? "#EF9F27" : "#D85A30"}80`,
                  }}
                >
                  {t === "SAFE" ? "2" : t === "COMPENSABLE" ? "1" : "1"}
                </span>
              </div>
            ))}
          </div>

          <div
            style={{
              flex: 1,
              padding: "16px 20px",
              fontFamily: "'Geist Mono','JetBrains Mono',monospace",
              fontSize: 12,
              lineHeight: "20px",
              overflow: "auto",
            }}
          >
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
              <span>python agent.py --customer alice</span>
            </div>

            <div style={{ marginTop: 12 }}>
              <span style={{ color: "#1D9E75" }}>● SAFE</span>{" "}
              <span style={{ color: "var(--text-code)" }}>lookup_customer</span>
              <span style={{ fontSize: 10, color: "var(--text-muted)", marginLeft: 6 }}>c_8a7b6c5d</span>
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              ✓ replayed from cache (call_signature match)
            </div>
            <div style={{ paddingLeft: 20, color: "#1D9E75", fontSize: 11 }}>
              → name: Alice Chen · plan: enterprise
            </div>

            <div style={{ marginTop: 10 }}>
              <span style={{ color: "#EF9F27" }}>● COMP.</span>{" "}
              <span style={{ color: "var(--text-code)" }}>create_ticket</span>
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              ⤵ compensation: undo_create_ticket
            </div>
            <div style={{ paddingLeft: 20, color: "#EF9F27", fontSize: 11 }}>
              → TKT-4421 · status: draft
            </div>

            <div style={{ marginTop: 10 }}>
              <span style={{ color: "#D85A30" }}>● IRREV.</span>{" "}
              <span style={{ color: "var(--text-code)" }}>send_email</span>
            </div>
            <div style={{ paddingLeft: 20, color: "#D85A30", fontSize: 11 }}>
              ⚡ approval_id: apr_9f8e, session suspended
            </div>
            <div style={{ paddingLeft: 20, color: "var(--text-muted)", fontSize: 11 }}>
              waiting for human decision...<span className="hero-mockup-cursor" />
            </div>
          </div>

          <div
            style={{
              width: "20%",
              borderLeft: "1px solid var(--border-subtle)",
              padding: 16,
              background: "var(--bg-surface)",
              overflowY: "auto",
            }}
          >
            {[
              { label: "SESSION", value: "a1b2c3d4", mono: true },
              { label: "STATUS", value: "awaiting_approval", dot: "#D85A30" },
              { label: "ORG", value: "org_demo" },
              { label: "CALLS", value: "4 total (2 SAFE · 1 COMP. · 1 IRREV.)" },
              { label: "DURATION", value: "12.4s" },
            ].map((r) => (
              <div key={r.label} style={{ marginBottom: 14 }}>
                <div
                  style={{
                    fontFamily: "'Geist Mono', monospace",
                    fontSize: 9,
                    color: "var(--text-muted)",
                    letterSpacing: "0.08em",
                    marginBottom: 2,
                    textTransform: "uppercase",
                  }}
                >
                  {r.label}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  {r.dot && (
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: r.dot,
                        flexShrink: 0,
                      }}
                    />
                  )}
                  <span
                    style={{
                      fontFamily: r.mono ? "'Geist Mono', monospace" : "'Geist', sans-serif",
                      fontSize: 13,
                      color: r.dot ? "#D85A30" : "var(--text-code)",
                      overflowWrap: "break-word",
                      wordBreak: "break-word",
                    }}
                  >
                    {r.value}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </Frame>
    </div>
  );
}
