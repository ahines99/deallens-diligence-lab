import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import "@/app/globals.css";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";

export const metadata: Metadata = {
  title: "DealLens Diligence Lab",
  description:
    "A public-data AI diligence copilot for investment research, red-flag detection, and IC memo generation.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
          <nav className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded bg-brand-600 text-[11px] font-bold text-white">
                DL
              </span>
              DealLens Diligence Lab
            </Link>
            <div className="flex items-center gap-1 text-sm">
              <Link
                href="/"
                className="rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              >
                Home
              </Link>
              <Link
                href="/workspaces"
                className="rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              >
                Workspaces
              </Link>
            </div>
          </nav>
        </header>
        <DisclaimerBanner />
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
