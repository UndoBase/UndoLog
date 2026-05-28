/**
 * AnimatedTerminal: Auto-playing terminal animation for the install section.
 * Cycles through pip install output lines with progress bars and cursor blink.
 * Restarts automatically after a pause.
 */
"use client";

import { useState, useEffect, useRef } from "react";
import CopyButton from "./CopyButton";

export type TerminalLine = {
  text: string;
  delay: number;
  barDuration?: number;
};

const BAR = "\u2501";

function splitProgressLine(text: string) {
  const barStart = text.indexOf(BAR);
  if (barStart === -1) return null;
  let barEnd = barStart;
  while (barEnd < text.length && text[barEnd] === BAR) barEnd++;
  return {
    prefix: text.slice(0, barStart),
    bar: text.slice(barStart, barEnd),
    suffix: text.slice(barEnd),
  };
}

export default function AnimatedTerminal({
  label,
  lines,
  copyCode,
  pauseMs = 5000,
}: {
  label: string;
  lines: TerminalLine[];
  copyCode: string;
  pauseMs?: number;
}) {
  const [visibleCount, setVisibleCount] = useState(0);
  const timeoutsRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const done = visibleCount >= lines.length;

  useEffect(() => {
    const run = () => {
      const ts: ReturnType<typeof setTimeout>[] = [];
      const totalDelay = lines.length > 0 ? lines[lines.length - 1].delay : 0;

      lines.forEach((_, i) => {
        const t = setTimeout(() => {
          setVisibleCount(lines.length - i - 1);
        }, 30 * (i + 1));
        ts.push(t);
      });

      const startDelay = 30 * lines.length + 200;
      lines.forEach((line, i) => {
        const t = setTimeout(() => {
          setVisibleCount(i + 1);
        }, startDelay + line.delay);
        ts.push(t);
      });

      const restartT = setTimeout(run, startDelay + totalDelay + pauseMs);
      ts.push(restartT);
      timeoutsRef.current = ts;
    };

    run();

    return () => timeoutsRef.current.forEach(clearTimeout);
  }, [lines, pauseMs]);

  return (
    <div className="code-block">
      <div className="code-block-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ display: "flex", gap: 5 }}>
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FF5F57" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#FEBC2E" }} />
            <span style={{ width: 9, height: 9, borderRadius: "50%", background: "#28C840" }} />
          </div>
          <span className="code-block-label">{label}</span>
        </div>
        <CopyButton code={copyCode} className="copy-btn" />
      </div>
      <pre className="code-block-pre-scroll">
        <code className="code-block-code">
          {lines.slice(0, visibleCount).map((line, i) => {
            const parts = splitProgressLine(line.text);
            if (parts) {
              return (
                <div key={i} className="code-block-line">
                  <span>{parts.prefix}</span>
                  <span
                    className="progress-bar-fill"
                    style={{ "--bar-ms": (line.barDuration || 800) + "ms" } as React.CSSProperties}
                  >
                    {parts.bar}
                  </span>
                  <span>{parts.suffix}</span>
                </div>
              );
            }
            return <div key={i} className="code-block-line">{line.text}</div>;
          })}
          <div className="code-block-line">
            <span className="code-block-cursor" />
          </div>
        </code>
      </pre>
      <div className="code-block-status">
        <span>{label}</span>
        <span className={done ? "status-dot status-dot--done" : "status-dot status-dot--running"} />
        <span>{done ? "completed" : "installing..."}</span>
      </div>
    </div>
  );
}
