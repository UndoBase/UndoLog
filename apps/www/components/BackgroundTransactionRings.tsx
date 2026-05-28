/**
 * BackgroundTransactionRings: Concentric rings radiating from a focal point.
 * Each ring is a transaction boundary visualized as a topographic field.
 * A central dot + halo marks the "transaction initiation point."
 * stroke-dasharray on alternate rings creates a Morse-code-like "log entry" rhythm.
 */
export default function BackgroundTransactionRings() {
  const rings = [
    { r: 35, o: 0.07, dash: "3 18" },
    { r: 75, o: 0.05, dash: "2 22" },
    { r: 115, o: 0.04, dash: "1.5 26" },
    { r: 170, o: 0.025, dash: "1 30" },
  ];

  return (
    <>
      <div
        style={{
          position: "fixed",
          inset: 0,
          pointerEvents: "none",
          zIndex: -1,
          overflow: "hidden",
        }}
      >
        <svg
          style={{
            position: "absolute",
            left: "50%",
            bottom: "20%",
            transform: "translateX(-50%)",
            width: "180%",
            height: "140%",
            overflow: "visible",
          }}
          viewBox="-200 -200 400 400"
          preserveAspectRatio="xMidYMax slice"
        >
          {rings.map((ring, i) => (
            <circle
              key={i}
              cx="0"
              cy="0"
              r={ring.r}
              fill="none"
              stroke="var(--purple-primary)"
              strokeWidth={i % 2 === 0 ? 0.6 : 0.4}
              opacity={ring.o}
              strokeDasharray={ring.dash}
            />
          ))}
        </svg>

        {/* Edge fade: prevents rings from showing as arc segments at viewport edges */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `radial-gradient(ellipse 70% 70% at 50% 50%, transparent 30%, var(--bg-void) 75%)`,
          }}
        />
      </div>

      {/* Corner elements: positioned above content (z-index 10) but below navbar (z-index 100) */}
      <div
        style={{
          position: "fixed",
          inset: 0,
          pointerEvents: "none",
          zIndex: 10,
        }}
      >
        {/* Top-left: arcs open toward center (bottom-right) */}
        <div
          style={{
            position: "absolute",
            top: 86,
            left: 24,
            opacity: 0.25,
          }}
        >
          <svg width={64} height={64} viewBox="0 0 60 60" fill="none">
            <path d="M 0 56 A 56 56 0 0 0 56 0" stroke="var(--safe-green)" strokeWidth="1.2" strokeLinecap="round" strokeDasharray="4 8" />
            <path d="M 0 46 A 46 46 0 0 0 46 0" stroke="var(--compensable-amber)" strokeWidth="1" strokeLinecap="round" strokeDasharray="3 10" />
            <path d="M 0 36 A 36 36 0 0 0 36 0" stroke="var(--irreversible-coral)" strokeWidth="0.8" strokeLinecap="round" strokeDasharray="2 12" />
          </svg>
        </div>

        {/* Top-right: arcs open toward center (bottom-left) */}
        <div
          style={{
            position: "absolute",
            top: 86,
            right: 24,
            opacity: 0.25,
          }}
        >
          <svg width={64} height={64} viewBox="0 0 60 60" fill="none">
            <path d="M 4 0 A 56 56 0 0 0 60 56" stroke="var(--safe-green)" strokeWidth="1.2" strokeLinecap="round" strokeDasharray="4 8" />
            <path d="M 14 0 A 46 46 0 0 0 60 46" stroke="var(--compensable-amber)" strokeWidth="1" strokeLinecap="round" strokeDasharray="3 10" />
            <path d="M 24 0 A 36 36 0 0 0 60 36" stroke="var(--irreversible-coral)" strokeWidth="0.8" strokeLinecap="round" strokeDasharray="2 12" />
          </svg>
        </div>

        {/* Bottom-left: arcs open toward center (top-right) */}
        <div
          style={{
            position: "absolute",
            bottom: 24,
            left: 24,
            opacity: 0.25,
          }}
        >
          <svg width={64} height={64} viewBox="0 0 60 60" fill="none">
            <path d="M 0 4 A 56 56 0 0 1 56 60" stroke="var(--safe-green)" strokeWidth="1.2" strokeLinecap="round" strokeDasharray="4 8" />
            <path d="M 0 14 A 46 46 0 0 1 46 60" stroke="var(--compensable-amber)" strokeWidth="1" strokeLinecap="round" strokeDasharray="3 10" />
            <path d="M 0 24 A 36 36 0 0 1 36 60" stroke="var(--irreversible-coral)" strokeWidth="0.8" strokeLinecap="round" strokeDasharray="2 12" />
          </svg>
        </div>

        {/* Bottom-right: arcs open toward center (top-left) */}
        <div
          style={{
            position: "absolute",
            bottom: 24,
            right: 24,
            opacity: 0.25,
          }}
        >
          <svg width={64} height={64} viewBox="0 0 60 60" fill="none">
            <path d="M 4 60 A 56 56 0 0 1 60 4" stroke="var(--safe-green)" strokeWidth="1.2" strokeLinecap="round" strokeDasharray="4 8" />
            <path d="M 14 60 A 46 46 0 0 1 60 14" stroke="var(--compensable-amber)" strokeWidth="1" strokeLinecap="round" strokeDasharray="3 10" />
            <path d="M 24 60 A 36 36 0 0 1 60 24" stroke="var(--irreversible-coral)" strokeWidth="0.8" strokeLinecap="round" strokeDasharray="2 12" />
          </svg>
        </div>

        {/* Top-right: Amber wave (COMPENSABLE) */}
        <div
          style={{
            position: "absolute",
            top: 80,
            right: 24,
            opacity: 0.1,
          }}
        >
          <svg width={48} height={48} viewBox="0 0 60 60" fill="none">
            <path
              d="M 5 5 A 50 50 0 0 0 55 55"
              stroke="var(--safe-green)"
              strokeWidth="0.8"
              strokeLinecap="round"
              strokeDasharray="3 10"
            />
            <path
              d="M 10 10 A 45 45 0 0 0 50 50"
              stroke="var(--compensable-amber)"
              strokeWidth="0.6"
              strokeLinecap="round"
              strokeDasharray="2 12"
            />
            <path
              d="M 15 15 A 40 40 0 0 0 45 45"
              stroke="var(--irreversible-coral)"
              strokeWidth="0.5"
              strokeLinecap="round"
              strokeDasharray="1.5 14"
            />
          </svg>
        </div>

        {/* Bottom-left: Coral wave (IRREVERSIBLE; all three colors) */}
        <div
          style={{
            position: "absolute",
            bottom: 80,
            left: 24,
            opacity: 0.1,
          }}
        >
          <svg width={48} height={48} viewBox="0 0 60 60" fill="none">
            <path
              d="M 55 55 A 50 50 0 0 1 5 5"
              stroke="var(--irreversible-coral)"
              strokeWidth="0.8"
              strokeLinecap="round"
              strokeDasharray="3 10"
            />
            <path
              d="M 50 50 A 45 45 0 0 1 10 10"
              stroke="var(--compensable-amber)"
              strokeWidth="0.6"
              strokeLinecap="round"
              strokeDasharray="2 12"
            />
            <path
              d="M 45 45 A 40 40 0 0 1 15 15"
              stroke="var(--safe-green)"
              strokeWidth="0.5"
              strokeLinecap="round"
              strokeDasharray="1.5 14"
            />
          </svg>
        </div>

        {/* Bottom-right: Purple wave (brand; all three colors) */}
        <div
          style={{
            position: "absolute",
            bottom: 80,
            right: 24,
            opacity: 0.08,
          }}
        >
          <svg width={48} height={48} viewBox="0 0 60 60" fill="none">
            <path
              d="M 55 5 A 50 50 0 0 0 5 55"
              stroke="var(--purple-primary)"
              strokeWidth="0.8"
              strokeLinecap="round"
              strokeDasharray="3 10"
            />
            <path
              d="M 50 10 A 45 45 0 0 0 10 50"
              stroke="var(--compensable-amber)"
              strokeWidth="0.6"
              strokeLinecap="round"
              strokeDasharray="2 12"
            />
            <path
              d="M 45 15 A 40 40 0 0 0 15 45"
              stroke="var(--irreversible-coral)"
              strokeWidth="0.5"
              strokeLinecap="round"
              strokeDasharray="1.5 14"
            />
          </svg>
        </div>
      </div>
    </>
  );
}
