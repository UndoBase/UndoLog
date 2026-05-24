"use client";

import { useEffect, useRef, useState } from "react";
import type { DocHeading } from "@/lib/docs";

export default function TableOfContents({ headings }: { headings: DocHeading[] }) {
  const [activeId, setActiveId] = useState<string>("");
  const observerRef = useRef<IntersectionObserver | null>(null);
  const tocRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (headings.length === 0) return;

    const ids = headings.map((h) => h.id);

    // Track which heading was last entered from above
    let lastEntered = "";

    const handleIntersect = (entries: IntersectionObserverEntry[]) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          lastEntered = entry.target.id;
          setActiveId(entry.target.id);
          return;
        }
      }
      // If nothing is intersecting, keep the last entered heading
      if (lastEntered) {
        setActiveId(lastEntered);
      }
    };

    observerRef.current = new IntersectionObserver(handleIntersect, {
      // -80px top for navbar, -80% bottom so heading only needs to enter top 20%
      rootMargin: "-80px 0px -80% 0px",
      threshold: 0,
    });

    const elements = ids
      .map((id) => document.getElementById(id))
      .filter(Boolean) as HTMLElement[];

    elements.forEach((el) => observerRef.current!.observe(el));

    // Activate first heading initially
    if (elements.length > 0) {
      setActiveId(elements[0].id);
    }

    return () => {
      observerRef.current?.disconnect();
    };
  }, [headings]);

  // Auto-scroll TOC to keep active item visible
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
    <aside className="docs-toc">
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
  );
}
