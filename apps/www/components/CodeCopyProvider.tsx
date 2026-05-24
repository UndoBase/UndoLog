"use client";

import { useEffect, useRef } from "react";

export default function CodeCopyProvider({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = ref.current;
    if (!container) return;

    const figures = container.querySelectorAll<HTMLElement>("figure[data-rehype-pretty-code-figure]");

    figures.forEach((figure) => {
      if (figure.querySelector(".code-copy-btn")) return;

      const pre = figure.querySelector("pre");
      if (!pre) return;

      const code = pre.querySelector("code");
      if (!code) return;

      const id = `code-${Math.random().toString(36).slice(2, 9)}`;
      figure.dataset.codeId = id;

      const btn = document.createElement("button");
      btn.className = "code-copy-btn";
      btn.innerHTML = '<svg class="copy-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg><span class="copy-label">Copy</span>';
      btn.setAttribute("aria-label", "Copy code");

      btn.addEventListener("click", async () => {
        const text = code.textContent || "";
        try {
          await navigator.clipboard.writeText(text);
          btn.innerHTML = '<svg class="copy-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg><span class="copy-label">Copied</span>';
          btn.classList.add("copied");
          setTimeout(() => {
            btn.innerHTML = '<svg class="copy-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg><span class="copy-label">Copy</span>';
            btn.classList.remove("copied");
          }, 1500);
        } catch {
          // Clipboard API not available
        }
      });

      figure.style.position = "relative";
      figure.appendChild(btn);
    });
  }, []);

  return <div ref={ref}>{children}</div>;
}
