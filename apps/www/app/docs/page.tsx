import { getSidebar } from "@/lib/docs";
import DocsLayout from "@/components/DocsLayout";
import Link from "next/link";
import ArrowIcon from "@/components/ArrowIcon";

export default function DocsLanding() {
  const sidebar = getSidebar();

  return (
    <DocsLayout>
      <div className="docs-landing">
        <h1 className="docs-landing-title">UndoLog Documentation</h1>
        <p className="docs-landing-subtitle">
          Everything you need to understand, deploy, and extend UndoLog, the AI agent safe execution runtime.
        </p>

        <div className="docs-landing-grid">
          {sidebar.map((section) => {
            const sectionSlug = `/docs/${section.dir}`;
            const descriptions: Record<string, string> = {
              "getting-started": "Installation, quickstart, and core concepts to get you running fast.",
              "guides": "Step-by-step guidance for tool annotation, compensations, approval gates, and integrations.",
              "reference": "Complete API, configuration, and schema references.",
              "explanation": "Deep dives into the safety model, exactly-once semantics, and architectural decisions.",
              "adr": "Architecture Decision Records: why we built UndoLog this way.",
              "contributing": "How to set up a development environment, write docs, and run tests.",
              "changelog": "Version migration guides and release notes.",
            };

            return (
              <Link key={section.dir} href={sectionSlug} className="docs-landing-card">
                <h2 className="docs-landing-card-title">{section.label}</h2>
                <p className="docs-landing-card-desc">
                  {descriptions[section.dir] || `Browse ${section.label.toLowerCase()} documentation.`}
                </p>
                <span className="docs-landing-card-footer">
                  <span>{section.pages.length} {section.pages.length === 1 ? "page" : "pages"}</span>
                  <ArrowIcon size={12} />
                </span>
              </Link>
            );
          })}
        </div>
      </div>
    </DocsLayout>
  );
}
