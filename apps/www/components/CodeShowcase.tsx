"use client";

import { Highlight } from "prism-react-renderer";
import CopyButton from "./CopyButton";

const pythonCode = `from undolog_sdk import undolog_tool, ToolTier, CompensationDescriptor

@undolog_tool(tier=ToolTier.SAFE)
async def read_user(id: str) -> dict: ...

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("delete_user"),
)
async def create_user(payload: dict) -> dict: ...

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def send_payout(amount: float, to: str) -> bool: ...`;

const undologTheme = {
  plain: {
    color: "#e2e0f0",
    backgroundColor: "transparent",
    fontFamily: "'Geist Mono', 'JetBrains Mono', monospace",
    fontSize: 14,
  },
  styles: [
    { types: ["keyword"], style: { color: "#7F77DD" } },
    { types: ["function"], style: { color: "#e2e0f0" } },
    { types: ["string"], style: { color: "#4ADE80" } },
    { types: ["comment"], style: { color: "#6b7280", fontStyle: "italic" } },
    { types: ["builtin"], style: { color: "#7F77DD", opacity: 0.8 } },
    { types: ["number"], style: { color: "#F59E0B" } },
    { types: ["boolean"], style: { color: "#F59E0B" } },
    { types: ["class-name"], style: { color: "#6CB6FF" } },
    { types: ["decorator"], style: { color: "#7F77DD", opacity: 0.8 } },
    { types: ["attr-name"], style: { color: "#6CB6FF" } },
    { types: ["punctuation"], style: { color: "#8b87a0" } },
    { types: ["property"], style: { color: "#e2e0f0" } },
    { types: ["tag"], style: { color: "#7F77DD" } },
    { types: ["operator"], style: { color: "#8b87a0" } },
  ],
};

function CopyBtn({ code }: { code: string }) {
  return <CopyButton code={code} className="code-showcase-copy" />;
}

export default function CodeShowcase() {
  return (
    <div className="code-showcase">
      <div className="code-showcase-block">
        <div className="code-showcase-header">
          <span className="code-showcase-lang">Python</span>
          <span className="code-showcase-more">TypeScript, Go, Rust coming soon</span>
          <CopyBtn code={pythonCode} />
        </div>
        <Highlight code={pythonCode} language="python" theme={undologTheme as any}>
          {({ tokens, getLineProps, getTokenProps }) => (
            <pre className="code-showcase-pre">
              {tokens.map((line, i) => (
                <div key={i} {...getLineProps({ line })} className="code-showcase-line">
                  <span className="code-showcase-ln">{i + 1}</span>
                  <span className="code-showcase-content">
                    {line.length > 0 ? (
                      line.map((token, key) => (
                        <span key={key} {...getTokenProps({ token })} />
                      ))
                    ) : (
                      <span>&nbsp;</span>
                    )}
                  </span>
                </div>
              ))}
            </pre>
          )}
        </Highlight>
      </div>
    </div>
  );
}
