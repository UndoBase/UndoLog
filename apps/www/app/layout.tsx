import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "UndoLog - Bringing ACID to Agent Actions",
  description:
    "The AI agent safe execution runtime. Classify every action by reversibility. Enforce exactly-once rollback. Gate irreversible operations behind human approval.",
  openGraph: {
    title: "UndoLog - Bringing ACID to Agent Actions",
    description:
      "The AI agent safe execution runtime. Classify every action by reversibility. Enforce exactly-once rollback.",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
      </head>
      <body>{children}</body>
    </html>
  );
}
