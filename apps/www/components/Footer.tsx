import Link from "next/link";
import LogoSvg from "./LogoSvg";

/**
 * Footer: Site-wide footer with brand, navigation links, and copyright.
 * Shared across all pages via layout or direct inclusion.
 */
export default function Footer() {
  return (
    <footer className="footer container">
      <div className="footer-brand">
        <LogoSvg size={20} />
        UndoBase
      </div>
      <ul className="footer-links">
        <li><Link href="/product">Product</Link></li>
        <li><Link href="/docs">Docs</Link></li>
        <li><a href="https://github.com/UndoBase/UndoLog" target="_blank" rel="noopener noreferrer">GitHub</a></li>
      </ul>
      <span className="footer-copyright">&copy; 2026 UndoBase</span>
    </footer>
  );
}
