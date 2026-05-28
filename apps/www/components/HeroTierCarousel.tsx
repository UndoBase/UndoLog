/**
 * HeroTierCarousel: Auto-cycling hero display that rotates through the
 * three tool tiers (SAFE, COMPENSABLE, IRREVERSIBLE). Each slide shows a
 * Python code sample with the corresponding decorator, a tier badge, and
 * a short behavior description. Pauses on hover.
 */
"use client";

import { useState, useEffect, useRef } from "react";
import { KW, FN, ST, CY, BL } from "./Syntax";

const tiers = [
  {
    key: "SAFE",
    color: "#1D9E75",
    label: "Read-only",
    description: "No effect log. Auto-replayed on cache hit. Zero side effects.",
    code: (
      <>
        <div><KW>@undolog_tool</KW><CY>(tier=</CY><BL>ToolTier.SAFE</BL><CY>)</CY></div>
        <div><KW>async def</KW> <FN>get_customer</FN>(id: <KW>str</KW>) -&gt; <KW>dict</KW>:</div>
        <div><span style={{ paddingLeft: 16 }}>row = <KW>await</KW> <FN>db.fetch_one</FN>(</span></div>
        <div><span style={{ paddingLeft: 32 }}><ST>{"\"SELECT * FROM customers WHERE id = $1\""}</ST>, id</span></div>
        <div><span style={{ paddingLeft: 16 }}>)</span></div>
        <div><span style={{ paddingLeft: 16 }}><KW>return</KW> dict(row)</span></div>
      </>
    ),
  },
  {
    key: "COMPENSABLE",
    color: "#EF9F27",
    label: "Compensable",
    description: "Pre-registers an undo. Journaled to effect log. LIFO rollback on failure.",
    code: (
      <>
        <div><KW>@undolog_tool</KW><CY>(</CY></div>
        <div><span style={{ paddingLeft: 16 }}>tier=<BL>ToolTier.COMPENSABLE</BL>,</span></div>
        <div><span style={{ paddingLeft: 16 }}>compensation=<BL>CompensationDescriptor</BL>.<FN>new</FN>(<ST>{"\"undo_ticket\""}</ST>),</span></div>
        <div><CY>)</CY></div>
        <div><KW>async def</KW> <FN>create_ticket</FN>(amount: <KW>float</KW>) -&gt; <KW>dict</KW>:</div>
        <div><span style={{ paddingLeft: 16 }}>ticket = <KW>await</KW> <FN>db.insert</FN>(<ST>{"\"tickets\""}</ST>, ...)</span></div>
        <div><span style={{ paddingLeft: 16 }}><KW>return</KW> ticket</span></div>
      </>
    ),
  },
  {
    key: "IRREVERSIBLE",
    color: "#D85A30",
    label: "Irreversible",
    description: "External side effect. Human approval required. Session suspended until decision.",
    code: (
      <>
        <div><KW>@undolog_tool</KW><CY>(tier=</CY><BL>ToolTier.IRREVERSIBLE</BL><CY>)</CY></div>
        <div><KW>async def</KW> <FN>send_payout</FN>(to: <KW>str</KW>, amount: <KW>float</KW>) -&gt; <KW>bool</KW>:</div>
        <div><span style={{ paddingLeft: 16 }}><KW>return</KW> <KW>await</KW> <FN>payout_provider.transfer</FN>(</span></div>
        <div><span style={{ paddingLeft: 32 }}>recipient=to, amount_cents=<FN>int</FN>(amount * 100)</span></div>
        <div><span style={{ paddingLeft: 16 }}>)</span></div>
      </>
    ),
  },
];

export default function HeroTierCarousel() {
  const [active, setActive] = useState(0);
  const paused = useRef(false);

  useEffect(() => {
    if (paused.current) return;
    const id = setInterval(() => {
      setActive((i) => (i + 1) % tiers.length);
    }, 3200);
    return () => clearInterval(id);
  }, []);

  const tier = tiers[active];

  return (
    <div
      style={{
        borderRadius: 8,
        border: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(18, 17, 28, 0.6)",
        backdropFilter: "blur(8px)",
        width: 500,
        overflow: "hidden",
      }}
      onMouseEnter={() => { paused.current = true; }}
      onMouseLeave={() => { paused.current = false; }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 5,
          padding: "7px 10px",
          borderBottom: "1px solid rgba(255,255,255,0.04)",
        }}
      >
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#FF5F57" }} />
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#FEBC2E" }} />
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#28C840" }} />
        <span
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 5,
            padding: "2px 7px",
            borderRadius: 3,
            background: `${tier.color}14`,
            fontSize: 9,
            fontFamily: "'Geist Mono', monospace",
            fontWeight: 500,
            color: tier.color,
            letterSpacing: "0.06em",
            transition: "all 400ms ease",
          }}
        >
          <span style={{ width: 5, height: 5, borderRadius: "50%", background: tier.color }} />
          {tier.key}
        </span>
      </div>

      <div
        style={{
          padding: "14px 14px 10px",
          fontFamily: "'Geist Mono', 'JetBrains Mono', monospace",
          fontSize: 13,
          lineHeight: 1.55,
          color: "#e2e0f0",
          fontVariantLigatures: "none",
          minHeight: 192,
        }}
      >
        <div key={active} style={{ animation: "heroFadeIn 400ms ease" }}>
          {tier.code}
          <div style={{ display: "flex", alignItems: "center", gap: 2, marginTop: 2, height: 13 }}>
            <span
              style={{
                width: 5,
                height: 11,
                background: "var(--purple-primary)",
                animation: "blink 1s step-end infinite",
                display: "inline-block",
              }}
            />
          </div>
        </div>
      </div>

      <div
        style={{
          padding: "6px 12px 10px",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontFamily: "'Geist', sans-serif",
            fontWeight: 500,
            color: tier.color,
            transition: "color 400ms ease",
          }}
        >
          {tier.label}
        </span>
        <span
          style={{
            fontSize: 10,
            color: "rgba(255,255,255,0.35)",
            fontFamily: "'Geist', sans-serif",
          }}
        >
          {tier.description}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {tiers.map((t, i) => (
            <div
              key={t.key}
              style={{
                width: i === active ? 16 : 5,
                height: 5,
                borderRadius: 3,
                background: i === active ? tier.color : "rgba(255,255,255,0.1)",
                transition: "all 500ms ease",
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
