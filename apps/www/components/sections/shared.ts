import type { CSSProperties } from "react";

/** Content container: 1400px max-width with horizontal padding. */
export const C: CSSProperties = {
  maxWidth: 1400,
  margin: "0 auto",
  padding: "0 12px",
};

/** Section padding: standard vertical spacing for page sections. */
export const SEC: CSSProperties = {
  padding: "140px 0",
};

/** H2 heading: 40px weight 500, tight letter-spacing. */
export const H2: CSSProperties = {
  fontSize: 40,
  fontWeight: 500,
  color: "#FFFFFF",
  letterSpacing: "-0.028em",
  lineHeight: 1.22,
  marginBottom: 14,
};

/** Body paragraph: 17px secondary text, constrained width. */
export const BODY: CSSProperties = {
  fontSize: 17,
  fontWeight: 500,
  color: "var(--text-secondary)",
  lineHeight: 1.65,
  maxWidth: 400,
};


