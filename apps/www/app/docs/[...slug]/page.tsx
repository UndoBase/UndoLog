import { notFound } from "next/navigation";
import Link from "next/link";
import { getDocPage, getAllDocSlugs } from "@/lib/docs";
import DocsLayout from "@/components/DocsLayout";
import TableOfContents from "@/components/TableOfContents";
import CodeCopyProvider from "@/components/CodeCopyProvider";
import ArrowIcon from "@/components/ArrowIcon";


export async function generateStaticParams() {
  const slugs = await getAllDocSlugs();
  return slugs.map((slug) => ({ slug: slug.split("/") }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const slugStr = slug.join("/");
  const page = await getDocPage(slugStr);

  if (!page) return { title: "Not Found" };

  return {
    title: `${page.title}. UndoLog Docs`,
    description: page.description,
  };
}

export default async function DocPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const slugStr = slug.join("/");
  const page = await getDocPage(slugStr);

  if (!page) notFound();

  return (
    <DocsLayout>
      <article>
        <div className="doc-body">
          <div className="doc-content">
            <CodeCopyProvider>
              <div
                className="doc-markdown"
                dangerouslySetInnerHTML={{ __html: page.contentHtml }}
              />
            </CodeCopyProvider>
          </div>
          <TableOfContents headings={page.headings} />
        </div>

        <div className="doc-footer-nav">
          {page.prev && (
            <Link href={`/docs/${page.prev.slug}`} className="doc-nav-link doc-nav-prev">
              <span className="doc-nav-direction">Previous</span>
              <span className="doc-nav-title"><ArrowIcon flip />{page.prev.title}</span>
            </Link>
          )}
          {page.next && (
            <Link href={`/docs/${page.next.slug}`} className="doc-nav-link doc-nav-next">
              <span className="doc-nav-direction">Next</span>
              <span className="doc-nav-title">{page.next.title}<ArrowIcon /></span>
            </Link>
          )}
        </div>
      </article>
    </DocsLayout>
  );
}
