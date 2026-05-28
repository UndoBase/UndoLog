import { C, SEC, H2, BODY } from "./shared";
import ArrowIcon from "../ArrowIcon";
import AgentRunShowcase from "../AgentRunShowcase";

/**
 * SectionAgentRun: Terminal execution demo (FIG 1.1).
 * Shows real-time tool call classification with per-tier color coding.
 * Floating Effect Journal overlay tracks committed and pending effects.
 */
export default function SectionAgentRun() {
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
            <h2 style={H2}>Every tool call is intercepted, classified, and routed in real time.</h2>
          </div>
          <div style={{ paddingLeft: 24 }}>
            <p style={BODY}>
              Each tool call is intercepted at runtime, classified
              by tier, and routed through the correct safety path,
              without touching your agent logic.
            </p>
            <div style={{ marginTop: 16 }}>
              <a href="/docs/explanation/safety-model" className="ghost-link">See how it works<ArrowIcon /></a>
            </div>
          </div>
        </div>

        <AgentRunShowcase />
      </div>
    </section>
  );
}
