import fs from "fs";
import path from "path";
import matter from "gray-matter";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeStringify from "rehype-stringify";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypePrettyCode, { type Options } from "rehype-pretty-code";

const DOCS_ROOT = path.resolve(process.cwd(), "../../docs");

export interface DocHeading {
  id: string;
  text: string;
  level: number;
}

export interface DocPage {
  slug: string;
  title: string;
  description: string;
  contentHtml: string;
  headings: DocHeading[];
  section: string;
  sectionTitle: string;
  next: { slug: string; title: string } | null;
  prev: { slug: string; title: string } | null;
}

export interface SidebarSection {
  dir: string;
  label: string;
  pages: { slug: string; title: string }[];
}

interface SidebarConfig {
  sections: SidebarSection[];
}

let cachedSidebar: SidebarConfig | null = null;
let cachedSlugs: { slug: string; title: string }[] | null = null;

const undologTheme = {
  name: "undolog",
  type: "dark" as const,
  colors: {
    "editor.background": "#1D1C2E",
    "editor.foreground": "#e2e0f0",
  },
  tokenColors: [
    { scope: "keyword", settings: { foreground: "#7F77DD" } },
    { scope: "keyword.control", settings: { foreground: "#7F77DD" } },
    { scope: "storage.type", settings: { foreground: "#7F77DD" } },
    { scope: "storage.modifier", settings: { foreground: "#7F77DD" } },
    { scope: "string", settings: { foreground: "#4ADE80" } },
    { scope: "string.quoted", settings: { foreground: "#4ADE80" } },
    { scope: "string.regexp", settings: { foreground: "#F59E0B" } },
    { scope: "comment", settings: { foreground: "#6b7280", fontStyle: "italic" } },
    { scope: "entity.name.function", settings: { foreground: "#e2e0f0" } },
    { scope: "support.function", settings: { foreground: "#6CB6FF" } },
    { scope: "support.type", settings: { foreground: "#7F77DD" } },
    { scope: "constant.numeric", settings: { foreground: "#F59E0B" } },
    { scope: "constant.language", settings: { foreground: "#8b87a0" } },
    { scope: "constant.builtin", settings: { foreground: "#8b87a0" } },
    { scope: "invalid", settings: { foreground: "#F07178" } },
    { scope: "invalid.deprecated", settings: { foreground: "#F07178" } },
    { scope: "entity.other.attribute-name", settings: { foreground: "#6CB6FF" } },
    { scope: "variable.other.constant", settings: { foreground: "#8b87a0" } },
    { scope: "punctuation", settings: { foreground: "#8b87a0" } },
    { scope: "punctuation.definition.tag", settings: { foreground: "#7F77DD" } },
    { scope: "meta.tag", settings: { foreground: "#7F77DD" } },
  ],
};

const prettyCodeOptions = {
  theme: undologTheme as any,
  keepBackground: true,
} satisfies Options;

function readSidebarConfig(): SidebarConfig {
  if (cachedSidebar) return cachedSidebar;

  const rootSidebarPath = path.join(DOCS_ROOT, "_sidebar.json");
  const result: SidebarConfig = fs.existsSync(rootSidebarPath)
    ? JSON.parse(fs.readFileSync(rootSidebarPath, "utf-8"))
    : { sections: [] };

  cachedSidebar = result;
  return result;
}

function readSectionSidebar(sectionDir: string): { slug: string; title: string }[] {
  const sidebarPath = path.join(DOCS_ROOT, sectionDir, "_sidebar.json");
  if (!fs.existsSync(sidebarPath)) return [];
  const config = JSON.parse(fs.readFileSync(sidebarPath, "utf-8"));
  return (config.pages || []).map((p: { slug: string; title: string }) => ({
    slug: sectionDir + (p.slug === "index" ? "" : `/${p.slug}`),
    title: p.title,
  }));
}

function getAllPagesFlat(): { slug: string; title: string }[] {
  if (cachedSlugs) return cachedSlugs;

  const sidebar = readSidebarConfig();
  const pages: { slug: string; title: string }[] = [];
  for (const section of sidebar.sections) {
    const sectionPages = readSectionSidebar(section.dir);
    pages.push(...sectionPages);
  }
  cachedSlugs = pages;
  return pages;
}

export function getSidebar(): SidebarSection[] {
  return readSidebarConfig().sections.map((section) => ({
    ...section,
    pages: readSectionSidebar(section.dir),
  }));
}

function findAndParseDoc(slug: string): { content: string; data: Record<string, unknown> } | null {
  const possiblePaths = [
    path.join(DOCS_ROOT, `${slug}.md`),
    path.join(DOCS_ROOT, `${slug}/README.md`),
    path.join(DOCS_ROOT, `${slug}/index.md`),
  ];

  for (const p of possiblePaths) {
    if (fs.existsSync(p)) {
      const raw = fs.readFileSync(p, "utf-8");
      const parsed = matter(raw);
      return { content: parsed.content, data: parsed.data };
    }
  }
  return null;
}

export async function getDocPage(slug: string): Promise<DocPage | null> {
  const parsed = findAndParseDoc(slug);
  if (!parsed) return null;

  const allPages = getAllPagesFlat();
  const currentIndex = allPages.findIndex((p) => p.slug === slug);

  const section = slug.split("/")[0];
  const sidebar = getSidebar();
  const sectionInfo = sidebar.find((s) => s.dir === section);

  const title = (parsed.data.title as string) || "";
  const description = (parsed.data.description as string) || "";

  const processor = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(remarkRehype)
    .use(rehypeSlug)
    .use(rehypeAutolinkHeadings, { behavior: "wrap" })
    .use(rehypePrettyCode, prettyCodeOptions)
    .use(rehypeSanitize, defaultSchema)
    .use(rehypeStringify);

  const file = await processor.process(parsed.content);
  let contentHtml = String(file);

  // Strip .md from internal links so /docs/installation.md becomes /docs/installation.
  contentHtml = contentHtml.replace(/href="([^"]*)\.md(#.*)?"/g, 'href="$1$2"');

  // Make relative links absolute by prepending the section path.
  // Without this, /docs/guides resolves href="annotating-tools" to /docs/annotating-tools.
  contentHtml = contentHtml.replace(
    /href="(?!(?:https?:\/)?\/|#|mailto:)([^"]+)"/g,
    (match, href) => `href="/docs/${section}/${href}"`,
  );

  const headingRegex = /<h([2-3])\s+id="([^"]+)"[^>]*>(.*?)<\/h[2-3]>/g;
  const headings: DocHeading[] = [];
  let match;
  while ((match = headingRegex.exec(contentHtml)) !== null) {
    let headingText = match[3];
    while (headingText !== (headingText = headingText.replace(/<[^>]*>/g, "")));
    headings.push({
      level: parseInt(match[1]),
      id: match[2],
      text: headingText,
    });
  }

  return {
    slug,
    title,
    description,
    contentHtml,
    headings,
    section,
    sectionTitle: sectionInfo?.label || section,
    next: currentIndex < allPages.length - 1 ? allPages[currentIndex + 1] : null,
    prev: currentIndex > 0 ? allPages[currentIndex - 1] : null,
  };
}

export async function getAllDocSlugs(): Promise<string[]> {
  return getAllPagesFlat().map((p) => p.slug);
}
