"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { type SidebarSection } from "@/lib/docs";
import { sectionIcons } from "./sectionIcons";

export default function DocsSidebar({ sections }: { sections: SidebarSection[] }) {
  const pathname = usePathname();
  const currentSlug = pathname.replace(/^\/docs\/?/, "");

  return (
    <aside className="docs-sidebar">
      <nav className="docs-sidebar-nav">
        {sections.map((section) => {
          const sectionPath = `/docs/${section.dir}`;
          const isActive = currentSlug === section.dir || currentSlug.startsWith(section.dir + "/");

          return (
            <div key={section.dir} className="docs-sidebar-section">
              <Link
                href={sectionPath}
                className={`docs-sidebar-section-title ${isActive ? "active" : ""}`}
              >
                <span className="docs-sidebar-section-icon">
                  {sectionIcons[section.dir]}
                </span>
                {section.label}
              </Link>
              {isActive && (
                <div className="docs-sidebar-pages-wrap">
                  <div className="docs-sidebar-connector" />
                  <ul className="docs-sidebar-pages">
                    {section.pages.map((page) => {
                      const pageHref = page.slug === section.dir ? sectionPath : `/docs/${page.slug}`;
                      const isPageActive = pathname === pageHref;

                      return (
                        <li key={page.slug} className="docs-sidebar-page-item">
                          <div className={`docs-sidebar-page-indicator ${isPageActive ? "active" : ""}`} />
                          <Link
                            href={pageHref}
                            className={`docs-sidebar-link ${isPageActive ? "active" : ""}`}
                          >
                            {page.title}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
