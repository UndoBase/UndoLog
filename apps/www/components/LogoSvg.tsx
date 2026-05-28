/**
 * LogoSvg: Brand logo as an SVG arc with arrowhead and central dot.
 * Used in the Navbar brand link and Footer.
 */
export default function LogoSvg({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" className="logo-arc">
      <path
        d="M 80 60 A 40 40 0 1 0 18 38"
        fill="none"
        stroke="var(--purple-primary)"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray="320"
        strokeDashoffset="0"
        className="logo-arc-path"
      />
      <path d="M 18 38 L 28 30 L 30 42 Z" fill="var(--purple-primary)" />
      <circle cx="50" cy="50" r="9" fill="var(--purple-deep)" className="logo-dot" />
    </svg>
  );
}
