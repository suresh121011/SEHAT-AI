import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";

import "./globals.css";

// Atkinson Hyperlegible: designed for low-vision legibility (Braille Institute). Hindi/Odia text falls back to
// the system Devanagari/Odia fonts (see globals.css); they are not downloaded at build time.
const atkinson = Atkinson_Hyperlegible({ variable: "--font-atkinson", subsets: ["latin"], weight: ["400", "700"], display: "swap" });

export const metadata: Metadata = {
  title: "SEHAT AI",
  description: "Rules-first, non-diagnostic clinical triage and handoff — research prototype",
};

export const viewport: Viewport = { themeColor: "#0a3f40" };

// Chrome lives in the segment layouts: the app shell for /intake and /dashboard, a standalone page for /login.
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${atkinson.variable} min-h-screen antialiased`}>
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-card focus:px-3 focus:py-2">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
