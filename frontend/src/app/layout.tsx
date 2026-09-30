import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import { LogoutButton } from "@/components/LogoutButton";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SEHAT AI",
  description: "Rules-first, non-diagnostic clinical triage and handoff — research prototype",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased min-h-screen flex flex-col`}>
        <header className="border-b border-black/10 dark:border-white/15">
          <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              🏥 SEHAT AI
            </Link>
            <LogoutButton />
          </nav>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">{children}</main>
        <footer className="border-t border-black/10 px-4 py-3 text-center text-xs opacity-70 dark:border-white/15">
          Research prototype, not a clinically validated device. Non-diagnostic: a named human reviewer signs off
          every triage.
        </footer>
      </body>
    </html>
  );
}
