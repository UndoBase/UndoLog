import { ReactNode } from "react";

/** Keyword: purple (e.g. `def`, `return`, `await`). */
export const KW = (p: { children: ReactNode }) => (
  <span style={{ color: "#7F77DD" }}>{p.children}</span>
);
/** Function name: light grey (e.g. `lookup_customer`). */
export const FN = (p: { children: ReactNode }) => (
  <span style={{ color: "#e2e0f0" }}>{p.children}</span>
);
/** String literal: green. */
export const ST = (p: { children: ReactNode }) => (
  <span style={{ color: "#4ADE80" }}>{p.children}</span>
);
/** Comment: grey italic. */
export const CM = (p: { children: ReactNode }) => (
  <span style={{ color: "#6b7280", fontStyle: "italic" }}>
    {p.children}
  </span>
);
/** Built-in: muted purple. */
export const BL = (p: { children: ReactNode }) => (
  <span style={{ color: "#7F77DD", opacity: 0.8 }}>{p.children}</span>
);
/** Cyan/muted: punctuation, operators, constants. */
export const CY = (p: { children: ReactNode }) => (
  <span style={{ color: "#8b87a0" }}>{p.children}</span>
);
