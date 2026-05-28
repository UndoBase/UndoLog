"use client";

import Navbar from "@/components/Navbar";
import CodeShowcase from "@/components/CodeShowcase";
import AnimatedTerminal from "@/components/AnimatedTerminal";
import Footer from "@/components/Footer";
import { C, SEC, H2, BODY } from "@/components/sections/shared";
import ArrowIcon from "@/components/ArrowIcon";
import HeroTierCarousel from "@/components/HeroTierCarousel";

function TierArcIcon({ color }: { color: string }) {
  return (
    <svg width="24" height="24" viewBox="0 0 100 100" fill="none">
      <path
        d="M 80 60 A 40 40 0 1 0 18 38"
        stroke={color}
        strokeWidth="4"
        strokeLinecap="round"
      />
      <path d="M 18 38 L 28 30 L 30 42 Z" fill={color} />
    </svg>
  );
}

function GuaranteeIcon({ type }: { type: "rollback" | "stack" | "checkpoint" }) {
  const color = "var(--purple-primary)";
  if (type === "rollback") {
    return (
      <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
        <path d="M 32 36 L 24 28 L 32 20" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M 24 28 H 38 A 10 10 0 0 0 38 12 H 14" stroke={color} strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    );
  }
  if (type === "stack") {
    return (
      <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
        <rect x="8" y="10" width="32" height="6" rx="2" stroke={color} strokeWidth="2" />
        <rect x="10" y="20" width="28" height="6" rx="2" stroke={color} strokeWidth="2" />
        <rect x="12" y="30" width="24" height="6" rx="2" stroke={color} strokeWidth="2" />
      </svg>
    );
  }
  return (
    <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
      <path d="M 38 20 A 14 14 0 1 1 24 10" stroke={color} strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="24" cy="24" r="4" fill={color} />
      <path d="M 24 10 V 6 L 28 10 L 24 14 Z" fill={color} />
    </svg>
  );
}

export default function HomePage() {
  return (
    <>
      <Navbar />

      {/* ── Hero Section ────────────────────────────────────────────────── */}
      <section style={{ ...SEC, minHeight: "100vh", display: "flex", alignItems: "center", overflow: "hidden" }}>
        <div style={{ ...C, width: "100%" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 0.9fr",
              gap: 48,
              alignItems: "start",
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
                  marginBottom: 20,
                }}
              >
                The runtime that makes every<br />agent action reversible.
              </h1>
              <div style={{ marginTop: 48 }}>
                <HeroTierCarousel />
              </div>
            </div>
            <div style={{ paddingLeft: 24 }}>
              <p style={BODY}>
                Classify every action by reversibility. Enforce exactly-once
                rollback. Gate irreversible operations behind human approval.
              </p>
              <div style={{ marginTop: 24, display: "flex", gap: 24 }}>
                <a href="#tiers" className="ghost-link">
                  How it works<ArrowIcon />
                </a>
                <a
                  href="https://github.com/UndoBase/UndoLog"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ghost-link"
                >
                  View on GitHub<ArrowIcon />
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div className="section-divider" />

      {/* ── Three Tiers Section ─────────────────────────────────────────── */}
      <section style={{ ...SEC }} id="tiers">
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
              <h2 style={H2}>Every tool call has a tier.</h2>
            </div>
            <div style={{ paddingLeft: 24 }}>
              <p style={BODY}>
                Each tool call is classified at declaration time as safe, compensable,
                or irreversible, defining exactly how it can be rolled back.
              </p>
              <div style={{ marginTop: 16 }}>
                <a href="/docs/guides/annotating-tools" className="ghost-link">Learn about tiers<ArrowIcon /></a>
              </div>
            </div>
          </div>

          <div style={{
            display: "flex",
            gap: 24,
            marginBottom: 56,
          }}>
            {[
              { tier: "SAFE", color: "#1D9E75", title: "Safe", desc: "Read-only operations. No side effects. Executed without hesitation." },
              { tier: "COMPENSABLE", color: "#EF9F27", title: "Compensable", desc: "Writes with a known undo. Rollback is guaranteed and exactly-once." },
              { tier: "IRREVERSIBLE", color: "#D85A30", title: "Irreversible", desc: "Sending emails, payments, deletions. Gated behind human-in-the-loop." },
            ].map((c) => (
              <div key={c.tier} style={{
                flex: 1,
                background: "var(--bg-card)",
                border: "1px solid var(--border-light)",
                borderRadius: 10,
                padding: "28px 24px",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                  <TierArcIcon color={c.color} />
                  <span style={{
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.08em",
                    color: c.color,
                    fontFamily: "'Geist Mono', monospace",
                  }}>{c.tier}</span>
                </div>
                <h3 style={{
                  fontSize: 16,
                  fontWeight: 500,
                  color: "rgba(255,255,255,0.9)",
                  marginBottom: 6,
                }}>{c.title}</h3>
                <p style={{
                  fontSize: 13,
                  color: "var(--text-muted)",
                  lineHeight: 1.55,
                }}>{c.desc}</p>
              </div>
            ))}
          </div>

          <CodeShowcase />
        </div>
      </section>

      <div className="section-divider" />

      {/* ── Core Guarantees Section ──────────────────────────────────────── */}
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
              <h2 style={H2}>ACID guarantees for tool calls.</h2>
            </div>
            <div style={{ paddingLeft: 24 }}>
              <p style={BODY}>
                UndoLog provides database-grade safety guarantees for every
                agent action: rollback, replay, and audit built in.
              </p>
              <div style={{ marginTop: 16 }}>
                <a href="/docs/explanation/safety-model" className="ghost-link">Read the docs<ArrowIcon /></a>
              </div>
            </div>
          </div>

          <div style={{
            display: "flex",
            gap: 24,
          }}>
            {[
              { type: "rollback" as const, title: "Exactly-once rollback", desc: "If an undo is triggered, it runs exactly once: even if the system crashes mid-compensation. Backed by a persistent undo log." },
              { type: "stack" as const, title: "Persistent Undo Stack", desc: "Every action is journaled with its inverse. Replayable. Auditable. The stack survives restarts." },
              { type: "checkpoint" as const, title: "Human-in-the-loop", desc: "Irreversible actions automatically pause. A human must explicitly sign off. No silent disasters." },
            ].map((g) => (
              <div key={g.type} style={{
                flex: 1,
                padding: "28px 24px",
              }}>
                <div style={{ marginBottom: 16, opacity: 0.7 }}>
                  <GuaranteeIcon type={g.type} />
                </div>
                <h3 style={{
                  fontSize: 16,
                  fontWeight: 500,
                  color: "rgba(255,255,255,0.9)",
                  marginBottom: 6,
                }}>{g.title}</h3>
                <p style={{
                  fontSize: 13,
                  color: "var(--text-muted)",
                  lineHeight: 1.55,
                }}>{g.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="section-divider" />

      {/* ── Orbit System Section ─────────────────────────────────────────── */}
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
              <h2 style={H2}>The UndoBase system.</h2>
            </div>
            <div style={{ paddingLeft: 24 }}>
              <p style={BODY}>
                UndoLog is the first product: the innermost orbit of a platform
                built for safe agent execution.
              </p>
              <div style={{ marginTop: 16 }}>
                <a
                  href="https://github.com/UndoBase"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ghost-link"
                >
                  Explore UndoBase<ArrowIcon />
                </a>
              </div>
            </div>
          </div>

          <div style={{ display: "flex", justifyContent: "center", padding: "40px 0" }}>
            <svg viewBox="0 0 500 500" width="420" height="420" fill="none">
              <circle cx="250" cy="250" r="20" fill="var(--purple-primary)" opacity="0.15" />
              <defs>
                <radialGradient id="centerGlow">
                  <stop offset="0%" stopColor="var(--purple-primary)" stopOpacity="0.3" />
                  <stop offset="100%" stopColor="var(--purple-primary)" stopOpacity="0" />
                </radialGradient>
              </defs>
              <circle cx="250" cy="250" r="40" fill="url(#centerGlow)" />
              {[60, 110, 160, 210, 260].map((r, i) => (
                <circle key={i} cx="250" cy="250" r={r} stroke="rgba(127,119,221,0.15)" strokeWidth="1" strokeDasharray={i === 0 ? "none" : "4 6"} />
              ))}
              <text x="250" y="254" textAnchor="middle" fill="var(--purple-primary)" fontSize="12" fontFamily="Geist, sans-serif" fontWeight="600">UndoBase</text>
              <circle cx="250" cy="190" r="6" fill="var(--purple-primary)" />
              <text x="250" y="178" textAnchor="middle" fill="var(--purple-primary)" fontSize="11" fontFamily="Geist Mono, monospace" fontWeight="500">UndoLog</text>
              {[
                { label: "UndoScan", y: 140 },
                { label: "UndoRoute", y: 363 },
                { label: "UndoWatch", y: 410 },
              ].map((p, i) => (
                <g key={i}>
                  <circle cx="250" cy={p.y} r="4" fill="var(--text-muted)" />
                  <text x="250" y={p.y - 12} textAnchor="middle" fill="var(--text-muted)" fontSize="10" fontFamily="Geist Mono, monospace">{p.label}</text>
                </g>
              ))}
            </svg>
          </div>
        </div>
      </section>

      <div className="section-divider" />

      {/* ── Installation Section ────────────────────────────────────────── */}
      <section style={{ ...SEC }} id="install">
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
              <h2 style={H2}>Start in under a minute.</h2>
            </div>
            <div style={{ paddingLeft: 24 }}>
              <p style={BODY}>
                Install the SDK and classify your first tool call in seconds.
                No DSL, no scaffolding; just a single decorator.
              </p>
              <div style={{ marginTop: 16 }}>
                <a href="/docs/getting-started/installation" className="ghost-link">Get started<ArrowIcon /></a>
              </div>
            </div>
          </div>

          <AnimatedTerminal
            label="bash"
            copyCode="pip install undolog-sdk"
            lines={[
              { text: '$ pip install undolog-sdk', delay: 0 },
              { text: 'Collecting undolog-sdk', delay: 200 },
              { text: '  Downloading undolog_sdk-1.0.0-py3-none-any.whl (285 kB)', delay: 400 },
              { text: '     \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 285.0/285.0 kB 2.1 MB/s eta 0:00:00', delay: 800, barDuration: 600 },
              { text: 'Collecting compensation-protocol', delay: 1500 },
              { text: '  Downloading compensation_protocol-0.4.2-py3-none-any.whl (42 kB)', delay: 1700 },
              { text: '     \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 42.0/42.0 kB 1.8 MB/s eta 0:00:00', delay: 1950, barDuration: 250 },
              { text: 'Collecting undolog-core', delay: 2400 },
              { text: '  Downloading undolog_core-2.1.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (3.2 MB)', delay: 2600 },
              { text: '     \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 3.2/3.2 MB 4.5 MB/s eta 0:00:00', delay: 3400, barDuration: 1500 },
              { text: 'Building wheels for collected packages: undolog-core', delay: 5100 },
              { text: '  Building wheel for undolog-core (setup.py) ... done', delay: 6100 },
              { text: '  Created wheel for undolog-core: success', delay: 6300 },
              { text: 'Installing collected packages: undolog-core, compensation-protocol, undolog-sdk', delay: 6600 },
              { text: 'Successfully installed compensation-protocol-0.4.2 undolog-core-2.1.0 undolog-sdk-1.0.0', delay: 6900 },
            ]}
          />
        </div>
      </section>

      <div className="section-divider" />

      {/* ── Open Source Section ─────────────────────────────────────────── */}
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
              <h2 style={H2}>Apache 2.0. Free forever. Built in the open.</h2>
            </div>
            <div style={{ paddingLeft: 24 }}>
              <p style={BODY}>
                Star on GitHub and follow along. Grounded in ACRFence research.
                Built from first principles.
              </p>
              <div style={{ marginTop: 16 }}>
                <a
                  href="https://github.com/UndoBase/UndoLog"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ghost-link"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" style={{ marginRight: 2 }}>
                    <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                  </svg>
                  Star on GitHub<ArrowIcon />
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ──────────────────────────────────────────────────────── */}
      <Footer />
    </>
  );
}
