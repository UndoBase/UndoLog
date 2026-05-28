"use client";

import { Highlight } from "prism-react-renderer";
import CopyButton from "./CopyButton";

const pythonCode = `from undolog_sdk import undolog_tool, ToolTier, CompensationDescriptor

@undolog_tool(tier=ToolTier.SAFE)
async def read_user(id: str) -> dict:
    \"\"\"Retrieve a user by ID. Read-only, no side effects.\"\"\"
    row = await db.fetch_one(
        "SELECT * FROM users WHERE id = $1", id
    )
    if not row:
        raise UserNotFoundError(id)
    return dict(row)

@undolog_tool(
    tier=ToolTier.COMPENSABLE,
    compensation=CompensationDescriptor.new("delete_user"),
)
async def create_user(payload: dict) -> dict:
    \"\"\"Create a new user. Auto-compensated on failure.\"\"\"
    user_id = uuid4().hex
    await db.execute(
        "INSERT INTO users (id, name, email) "
        "VALUES ($1, $2, $3)",
        user_id, payload["name"], payload["email"],
    )
    return {"id": user_id, **payload}

@undolog_tool(tier=ToolTier.IRREVERSIBLE)
async def send_payout(amount: float, to: str) -> bool:
    \"\"\"Send a payout. Cannot be undone.\"\"\"
    await payout_provider.transfer(
        recipient=to,
        amount_cents=int(amount * 100),
    )
    return True`;

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

/**
 * CodeShowcase: Single-file code display with syntax highlighting
 * via prism-react-renderer. Shows a complete Python example annotated
 * with all three tool tiers. Includes line numbers, status bar, and copy button.
 */
export default function CodeShowcase() {
  return (
    <div className="code-showcase">
      <div className="code-showcase-block">
        <div className="code-showcase-header">
          <div style={{ display: "flex", gap: 5, marginRight: 4 }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FF5F57" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FEBC2E" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#28C840" }} />
          </div>
          <span className="code-showcase-lang">Python</span>
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
              <div className="code-showcase-line">
                <span className="code-showcase-ln">{tokens.length + 1}</span>
                <span className="code-showcase-content">
                  <span className="code-showcase-cursor" />
                </span>
              </div>
            </pre>
          )}
        </Highlight>
        <div className="code-showcase-status">
          <span>Python 3.12</span>
          <span>UTF-8</span>
          <span>Ln {pythonCode.trimEnd().split('\n').length + 1}, Col 1</span>
        </div>
      </div>
    </div>
  );
}
