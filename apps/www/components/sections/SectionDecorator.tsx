import DecoratorShowcase from "../DecoratorShowcase";
import { C, SEC, H2, BODY } from "./shared";
import ArrowIcon from "../ArrowIcon";

/**
 * SectionDecorator: Product page section showcasing the decorator-based
 * tier annotation pattern with the interactive DecoratorShowcase component.
 */
export default function SectionDecorator() {
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
            <h2 style={H2}>A single decorator classifies every tool call by reversibility tier.</h2>
          </div>
          <div style={{ paddingLeft: 24 }}>
            <p style={BODY}>
              Mark any async function by its reversibility tier:
              idempotency, effect logging, and compensation wiring
              are handled automatically.
            </p>
            <div style={{ marginTop: 16 }}>
              <a href="/docs/guides/annotating-tools" className="ghost-link">View the API<ArrowIcon /></a>
            </div>
          </div>
        </div>

        <DecoratorShowcase />
      </div>
    </section>
  );
}
