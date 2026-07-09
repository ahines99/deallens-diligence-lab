import type { Metadata } from "next";
import Link from "next/link";
import { Inter, Newsreader } from "next/font/google";
import "@/app/globals.css";

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});
const serif = Newsreader({
  subsets: ["latin"],
  variable: "--font-serif",
  display: "swap",
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
});

export const metadata: Metadata = {
  title: "DealLens Diligence Lab",
  description:
    "A public-data AI diligence copilot for investment research, red-flag detection, and IC memo generation.",
};

function Mark() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5" opacity="0.5" />
      <circle cx="12" cy="12" r="3.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable}`}>
      <body className="min-h-screen bg-paper font-sans text-body">
        <header className="sticky top-0 z-30 border-b border-black/20 bg-ink text-white/90">
          <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-6 px-5 lg:px-8">
            <Link href="/" className="group flex items-center gap-2.5 text-white">
              <span className="text-white/85 transition group-hover:text-white">
                <Mark />
              </span>
              <span className="flex items-baseline gap-2">
                <span className="font-serif text-[1.05rem] font-semibold tracking-tight">
                  DealLens
                </span>
                <span className="hidden text-2xs font-semibold uppercase tracking-eyebrow text-white/45 sm:inline">
                  Diligence Lab
                </span>
              </span>
            </Link>
            <nav className="ml-2 flex items-center gap-1 text-sm">
              <Link
                href="/"
                className="rounded px-2.5 py-1.5 text-white/70 transition hover:bg-white/10 hover:text-white"
              >
                Overview
              </Link>
              <Link
                href="/workspaces"
                className="rounded px-2.5 py-1.5 text-white/70 transition hover:bg-white/10 hover:text-white"
              >
                Workspaces
              </Link>
            </nav>
            <div className="ml-auto hidden items-center gap-2 text-2xs uppercase tracking-eyebrow text-white/40 md:flex">
              <span>SEC EDGAR</span>
              <span className="text-white/20">·</span>
              <span>FRED</span>
              <span className="text-white/20">·</span>
              <span>USAspending</span>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1400px] px-5 py-8 lg:px-8">{children}</main>

        <footer className="mt-12 border-t border-line bg-white">
          <div className="mx-auto flex max-w-[1400px] flex-col gap-1 px-5 py-6 text-2xs text-muted lg:px-8">
            <p className="uppercase tracking-eyebrow text-faint">Independent portfolio project</p>
            <p className="max-w-measure leading-relaxed">
              DealLens Diligence Lab uses public data (SEC EDGAR, FRED, USAspending) and is not
              affiliated with any firm. Outputs are AI-assisted drafts for human review —{" "}
              <span className="font-semibold text-body">not investment advice</span>. Qualitative
              severities are heuristic; market/valuation data is omitted.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
