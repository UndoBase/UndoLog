import { getSidebar } from "@/lib/docs";
import DocsSidebar from "@/components/DocsSidebar";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";

/**
 * DocsLayout: Page layout for all /docs routes.
 * Renders Navbar, sidebar navigation, main content area, and Footer.
 * Sidebar sections are generated from the docs content tree.
 */
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  const sidebar = getSidebar();

  return (
    <div className="docs-layout">
      <Navbar />
      <div className="docs-container">
        <DocsSidebar sections={sidebar} />
        <main className="docs-main">{children}</main>
      </div>
      <Footer />
    </div>
  );
}
