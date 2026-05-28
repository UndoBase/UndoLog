"use client";

import { useEffect, useRef, useState } from "react";
import type { DocHeading } from "@/lib/docs";

/**
 * TableOfContents: Collapsible sidebar navigation for doc articles.
 * Tracks active heading via IntersectionObserver and auto-scrolls
 * the TOC to keep the active item visible.
 * Toggle button hides/shows the panel with a smooth slide transition.
 */
export default function TableOfContents({ headings }: { headings: DocHeading[] }) {
  const [activeId, setActiveId] = useState<string>("");
  const [open, setOpen] = useState(true);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const tocRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (headings.length === 0) return;

    const ids = headings.map((h) => h.id);

    let lastEntered = "";

    const handleIntersect = (entries: IntersectionObserverEntry[]) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          lastEntered = entry.target.id;
          setActiveId(entry.target.id);
          return;
        }
      }
      if (lastEntered) {
        setActiveId(lastEntered);
      }
    };

    observerRef.current = new IntersectionObserver(handleIntersect, {
      rootMargin: "-80px 0px -80% 0px",
      threshold: 0,
    });

    const elements = ids
      .map((id) => document.getElementById(id))
      .filter(Boolean) as HTMLElement[];

    elements.forEach((el) => observerRef.current!.observe(el));

    if (elements.length > 0) {
      setActiveId(elements[0].id);
    }

    return () => {
      observerRef.current?.disconnect();
    };
  }, [headings]);

  useEffect(() => {
    if (!activeId || !tocRef.current) return;

    const activeLink = tocRef.current.querySelector(`.docs-toc-link[href="#${activeId}"]`);
    if (!activeLink) return;

    const toc = tocRef.current.closest(".docs-toc");
    if (!toc) return;

    const linkRect = activeLink.getBoundingClientRect();
    const tocRect = toc.getBoundingClientRect();

    const linkTop = linkRect.top - tocRect.top;
    const linkBottom = linkRect.bottom - tocRect.top;

    if (linkTop < 0 || linkBottom > tocRect.height - 20) {
      activeLink.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [activeId]);

  if (headings.length === 0) return null;

  return (
    <div className="docs-toc-wrap">
      <button
        className="docs-toc-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Hide table of contents" : "Show table of contents"}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          {open ? (
            <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          ) : (
            <>
              <path d="M2 4H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <path d="M2 8H14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <path d="M2 12H10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </>
          )}
        </svg>
        <span className="docs-toc-toggle-tip">{open ? "Hide" : "Contents"}</span>
      </button>

      <aside className={`docs-toc ${open ? "open" : "closed"}`}>
        <h4 className="docs-toc-title">On this page</h4>
        <nav>
          <ul className="docs-toc-list" ref={tocRef}>
            {headings.map((h) => {
              const isActive = activeId === h.id;
              return (
                <li
                  key={h.id}
                  className={`docs-toc-item ${isActive ? "active" : ""}`}
                  style={{ paddingLeft: h.level === 3 ? 12 : 0 }}
                >
                  <a
                    href={`#${h.id}`}
                    className={`docs-toc-link ${isActive ? "active" : ""}`}
                  >
                    <span>{h.text}</span>
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
    </div>
  );
}
