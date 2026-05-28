import { ReactNode } from "react";

/**
 * Frame: macOS-style chrome window frame.
 * Renders a bordered container with traffic-light buttons and an optional label bar.
 */
export function Frame({
  children,
  label,
  style: s,
  noTitle,
}: {
  children: ReactNode;
  label?: string;
  style?: React.CSSProperties;
  noTitle?: boolean;
}) {
  return (
    <div
      style={{
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        border: "1px solid var(--border-primary)",
        background: "var(--bg-surface)",
        boxShadow: "var(--shadow-card)",
        position: "relative",
        ...s,
      }}
    >
      {!noTitle && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "9px 14px",
            background: "var(--bg-header)",
            borderBottom: "1px solid var(--border-primary)",
          }}
        >
          <div style={{ display: "flex", gap: 5 }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FF5F57" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FEBC2E" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#28C840" }} />
          </div>
          {label && (
            <span
              style={{
                fontFamily: "'Geist Mono', monospace",
                fontSize: 10,
                color: "rgba(255,255,255,0.2)",
                marginLeft: 8,
                letterSpacing: "0.04em",
              }}
            >
              {label}
            </span>
          )}
        </div>
      )}
      {children}
    </div>
  );
}

/**
 * FloatCard: Floating overlay card with elevated shadow.
 * Used for tooltips, inspectors, and overlays positioned over Frame content.
 */
export function FloatCard({
  children,
  style: s,
}: {
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--border-overlay)",
        background: "var(--bg-surface)",
        boxShadow: "var(--shadow-floating)",
        ...s,
      }}
    >
      {children}
    </div>
  );
}
