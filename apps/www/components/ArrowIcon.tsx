/**
 * ArrowIcon: Inline arrow SVG used on ghost links throughout the site.
 * Supports a `flip` prop to rotate the arrow 180° for "previous" navigation.
 */
export default function ArrowIcon({ size = 14, flip }: { size?: number; flip?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" style={{
      flexShrink: 0,
      ...(flip ? { marginRight: 4, transform: "rotate(180deg)" } : { marginLeft: 4 }),
    }}>
      <path d="M3 8H13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M9 4L13 8L9 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
