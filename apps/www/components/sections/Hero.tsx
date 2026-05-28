import { C } from "./shared";
import ArrowIcon from "../ArrowIcon";
import MonitorMockup from "../MonitorMockup";

/**
 * Hero: Landing hero for the product showcase.
 * Animated grid background, three-tier badges (SAFE / COMPENSABLE / IRREVERSIBLE),
 * and a 3-panel agent monitor mockup with sidebar, terminal output, and metadata panel.
 */
export default function Hero() {
  return (
    <section
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
        overflow: "hidden",
        padding: "120px 12px 140px",
      }}
    >
      <div style={{ ...C, position: "relative", width: "100%" }}>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 0.9fr",
            gap: 48,
            alignItems: "start",
            marginBottom: 64,
          }}
        >
          <div>
            <h1
              style={{
                fontSize: 48,
                fontWeight: 500,
                color: "#FFFFFF",
                letterSpacing: "-0.035em",
                lineHeight: 1.08,
              }}
            >
              Declare the tier.<br />The runtime does the rest.
            </h1>
          </div>
          <div style={{ paddingBottom: 6, paddingLeft: 24 }}>
            <p
              style={{
                fontSize: 15,
                color: "var(--text-secondary)",
                lineHeight: 1.65,
                marginBottom: 24,
                maxWidth: 400,
              }}
            >
              Every tool call is classified as SAFE, compensable,
              or irreversible, with no fragile rollback code and
              no runaway agents.
            </p>
            <div style={{ display: "flex", gap: 24 }}>
              <a
                href="/docs/getting-started/installation"
                className="ghost-link"
              >
                Get started<ArrowIcon />
              </a>
            </div>
          </div>
        </div>

        <MonitorMockup />
      </div>
    </section>
  );
}
