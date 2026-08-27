import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "commentToFix",
  description: "Comment on a live site; an agent ships a fix to a preview deployment.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
