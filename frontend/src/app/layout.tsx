import type { Metadata } from "next";
import { Atkinson_Hyperlegible } from "next/font/google";
import Link from "next/link";

import { LogoutButton } from "@/components/LogoutButton";
import "./globals.css";

// Atkinson Hyperlegible: designed for low-vision legibility (Braille Institute). Hindi/Odia text falls back to
// the system Devanagari/Odia fonts (see globals.css); they are not downloaded at build time.
const atkinson = Atkinson_Hyperlegible({ variable: "--font-atkinson", subsets: ["latin"], weight: ["400", "700"], display: "swap" });

export const metadata: Metadata = {
  title: "SEHAT AI",
  description: "Rules-first, non-diagnostic clinical triage and handoff — research prototype",
};

function Wordmark() {
  return (
    <span className="flex items-center gap-2 font-bold tracking-tight text-primary">
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true" focusable="false">
        <rect x="1" y="1" width="30" height="30" rx="8" fill="currentColor" />
        <path d="M13 8h6v5h5v6h-5v5h-6v-5H8v-6h5V8Z" fill="#fff" />
      </svg>
      <span className="text-lg text-ink">
        SEHAT <span className="text-primary">AI</span>
      </span>
    </span>
  );
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${atkinson.variable} flex min-h-screen flex-col antialiased`}>
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-card focus:px-3 focus:py-2">
          Skip to main content
        </a>
        <header className="border-b border-subtle bg-card">
          <nav className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-3" aria-label="Site">
            <Link href="/" aria-label="SEHAT AI home" className="rounded">
              <Wordmark />
            </Link>
            <LogoutButton />
          </nav>
        </header>
        <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:py-8">
          {children}
        </main>
        <footer className="border-t border-subtle bg-card px-4 py-4 text-center text-sm text-muted">
          Research prototype, not a clinically validated device. Non-diagnostic: urgency comes from fixed rules, a health worker
          enters and checks every value, and a medical officer reviews and signs off each result on the reviewer dashboard.
        </footer>
      </body>
    </html>
  );
}
