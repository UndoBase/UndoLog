import { getSidebar } from "@/lib/docs";
import DocsSidebar from "@/components/DocsSidebar";
import Navbar from "@/components/Navbar";

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  const sidebar = getSidebar();

  return (
    <div className="docs-layout">
      <Navbar />
      <div className="docs-container">
        <DocsSidebar sections={sidebar} />
        <main className="docs-main">{children}</main>
      </div>
    </div>
  );
}
