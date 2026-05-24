"use client";

import Navbar from "@/components/Navbar";
import CodeShowcase from "@/components/CodeShowcase";
import LogoSvg from "@/components/LogoSvg";
import CopyButton from "@/components/CopyButton";

function LargeArcSvg({ size = 120 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100">
      <path
        d="M 80 60 A 40 40 0 1 0 18 38"
        fill="none"
        stroke="var(--purple-primary)"
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray="320"
        strokeDashoffset="0"
        className="hero-arc-path"
      />
      <path
        d="M 18 38 L 28 30 L 30 42 Z"
        fill="var(--purple-primary)"
      />
      <circle cx="50" cy="50" r="7" fill="var(--purple-deep)" />
    </svg>
  );
}

function ConcentricArcsBg() {
  return (
    <svg viewBox="0 0 800 800" fill="none" xmlns="http://www.w3.org/2000/svg">
      {[60, 100, 140, 180, 220, 260, 300, 340].map((r, i) => (
        <circle
          key={i}
          cx="400"
          cy="400"
          r={r}
          stroke="var(--purple-primary)"
          strokeWidth="0.5"
          opacity={0.12 - i * 0.01}
        />
      ))}
    </svg>
  );
}

function TierArcIcon({ color }: { color: string }) {
  return (
    <svg width="24" height="24" viewBox="0 0 100 100" fill="none" className="tier-arc-icon">
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

function InstallBlock({ label, code }: { label: string; code: string }) {
  return (
    <div className="code-block">
      <div className="code-block-header">
        <span>{label}</span>
        <CopyButton code={code} className="copy-btn" />
      </div>
      <pre>
        <code className="code-block-code">{code}</code>
      </pre>
    </div>
  );
}

export default function HomePage() {
  return (
    <>
      <style>{`
        .logo-arc:hover .logo-arc-path {
          animation: glow-pulse 300ms cubic-bezier(0.16, 1, 0.3, 1);
        }
        .logo-arc:hover .logo-dot {
          box-shadow: 0 0 12px var(--purple-primary);
        }
        .hero-arc-path {
          stroke-dashoffset: 320;
          animation: arc-draw 600ms cubic-bezier(0.16, 1, 0.3, 1) 1.2s forwards;
        }
        .hero-arc-visual .hero-arc-path {
          animation-delay: 1.6s;
        }
      `}</style>

      <Navbar />

      {/* ── Hero Section ────────────────────────────────────────────────── */}
      <section className="hero">
        <div className="hero-bg">
          <div className="hero-grid" />
          <div className="hero-arcs">
            <ConcentricArcsBg />
          </div>
        </div>

        <div className="hero-content">
          <div className="hero-arc-visual">
            <LargeArcSvg size={120} />
          </div>
          <div className="hero-text">
            <p className="hero-eyebrow">UndoLog</p>
            <h1 className="hero-headline">Bringing ACID to agent actions.</h1>
            <p className="hero-subheadline">
              The AI agent safe execution runtime. Classify every action by reversibility.
              Enforce exactly-once rollback. Gate irreversible operations behind human approval.
            </p>
            <div className="hero-cta">
            <a href="#install" className="btn btn-primary">
              Get Started
              <svg className="btn-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 5l7 7-7 7" />
              </svg>
            </a>
              <a
                href="https://github.com/UndoBase/UndoLog"
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-secondary"
              >
                View on GitHub
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* ── Three Tiers Section ─────────────────────────────────────────── */}
      <section className="section tiers-section" id="tiers">
        <div className="container">
          <div className="section-heading">
            <h2>Every tool call has a tier.</h2>
          </div>

          <div className="tiers-grid">
            <div className="tier-card tier-card-safe">
              <TierArcIcon color="var(--safe-green)" />
              <span className="tier-indicator">SAFE</span>
              <h3>Safe</h3>
              <p>Read-only operations. No side effects. Executed without hesitation.</p>
            </div>

            <div className="tier-card tier-card-compensable">
              <TierArcIcon color="var(--compensable-amber)" />
              <span className="tier-indicator">COMPENSABLE</span>
              <h3>Compensable</h3>
              <p>Writes with a known undo. Rollback is guaranteed and exactly-once.</p>
            </div>

            <div className="tier-card tier-card-irreversible">
              <TierArcIcon color="var(--irreversible-coral)" />
              <span className="tier-indicator">IRREVERSIBLE</span>
              <h3>Irreversible</h3>
              <p>Sending emails, payments, deletions. Gated behind human-in-the-loop. The arc pulses until you approve.</p>
            </div>
          </div>

          <CodeShowcase />
        </div>
      </section>

      {/* ── Core Guarantees Section ─────────────────────────────────────── */}
      <section className="section guarantees-section">
        <div className="guarantees-bg-arcs">
          <svg viewBox="0 0 900 900" fill="none">
            {[100, 160, 220, 280, 340].map((r, i) => (
              <circle key={i} cx="450" cy="450" r={r} stroke="var(--purple-primary)" strokeWidth="0.5" />
            ))}
          </svg>
        </div>

        <div className="container">
          <div className="section-heading">
            <h2>ACID guarantees for tool calls.</h2>
          </div>

          <div className="guarantees-grid">
            <div className="guarantee-card">
              <div className="icon"><GuaranteeIcon type="rollback" /></div>
              <h3>Exactly-once rollback</h3>
              <p>If an undo is triggered, it runs exactly once: even if the system crashes mid-compensation. Backed by a persistent undo log.</p>
            </div>

            <div className="guarantee-card">
              <div className="icon"><GuaranteeIcon type="stack" /></div>
              <h3>Persistent Undo Stack</h3>
              <p>Every action is journaled with its inverse. Replayable. Auditable. The stack survives restarts.</p>
            </div>

            <div className="guarantee-card">
              <div className="icon"><GuaranteeIcon type="checkpoint" /></div>
              <h3>Human-in-the-loop checkpoint</h3>
              <p>Irreversible actions automatically pause. A human must explicitly sign off. No silent disasters.</p>
            </div>
          </div>
        </div>
      </section>

      {/* ── Orbit System Section ────────────────────────────────────────── */}
      <section className="section orbit-section">
        <div className="container">
          <div className="section-heading">
            <h2>The UndoBase system.</h2>
            <p>UndoLog is the first product: the innermost orbit of a platform built for safe agent execution.</p>
          </div>

          <div className="orbit-diagram">
            <svg viewBox="0 0 500 500" fill="none">
              {/* Center glow */}
              <circle cx="250" cy="250" r="20" fill="var(--purple-primary)" opacity="0.15" />
              <defs>
                <radialGradient id="centerGlow">
                  <stop offset="0%" stopColor="var(--purple-primary)" stopOpacity="0.3" />
                  <stop offset="100%" stopColor="var(--purple-primary)" stopOpacity="0" />
                </radialGradient>
              </defs>
              <circle cx="250" cy="250" r="40" fill="url(#centerGlow)" />

              {/* Orbit rings */}
              {[60, 110, 160, 210, 260].map((r, i) => (
                <circle key={i} cx="250" cy="250" r={r} stroke="rgba(127,119,221,0.15)" strokeWidth="1" strokeDasharray={i === 0 ? "none" : "4 6"} />
              ))}

              {/* Center label */}
              <text x="250" y="254" textAnchor="middle" fill="var(--purple-primary)" fontSize="12" fontFamily="Geist, sans-serif" fontWeight="600">UndoBase</text>

              {/* Orbit 1 - UndoLog (highlighted) */}
              <circle cx="250" cy="190" r="6" fill="var(--purple-primary)" />
              <text x="250" y="178" textAnchor="middle" fill="var(--purple-primary)" fontSize="11" fontFamily="Geist Mono, monospace" fontWeight="500">UndoLog</text>

              {/* Outer orbits - dimmed placeholders */}
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

      {/* ── Installation Section ────────────────────────────────────────── */}
      <section className="section install-section" id="install">
        <div className="container">
          <div className="section-heading">
            <h2>Start in under a minute.</h2>
          </div>

          <div className="install-grid">
            <InstallBlock label="Install" code="pip install undolog-sdk" />
            <InstallBlock label="Usage" code={'from undolog import undolog_tool\n\n@undolog_tool(tier="safe")\ndef read_user(id: str) -> dict:\n    ...'} />
          </div>
        </div>
      </section>

      {/* ── Open Source Section ─────────────────────────────────────────── */}
      <section className="section opensource-section">
        <div className="container">
          <h2>Apache 2.0. Free forever. Built in the open.</h2>
          <p className="opensource-text">Star on GitHub and follow along.</p>

          <div style={{ marginTop: 32, display: "flex", justifyContent: "center", gap: 16, flexWrap: "wrap" }}>
            <a
              href="https://github.com/UndoBase/UndoLog"
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-primary"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              Star on GitHub
            </a>
          </div>

          <p className="opensource-footnote">
            Backed by the ACRFence research. Built from first principles. Apache 2.0.
          </p>
        </div>
      </section>

      {/* ── Footer ──────────────────────────────────────────────────────── */}
      <footer className="footer container">
        <div className="footer-brand">
          <LogoSvg size={20} />
          UndoBase
        </div>
        <ul className="footer-links">
          <li><a href="/docs">Docs</a></li>
          <li><a href="https://github.com/UndoBase/UndoLog" target="_blank" rel="noopener noreferrer">GitHub</a></li>
        </ul>
        <span className="footer-copyright">&copy; 2026 UndoBase</span>
      </footer>
    </>
  );
}
