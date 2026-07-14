import Link from "next/link";
import type { ReactNode } from "react";

export function AuthShell({ eyebrow, title, subtitle, children, footer }: { eyebrow: string; title: string; subtitle: string; children: ReactNode; footer: ReactNode }) {
  return (
    <div className="mx-auto grid min-h-[68vh] max-w-5xl overflow-hidden rounded-md border border-line bg-panel shadow-md lg:grid-cols-[.9fr_1.1fr]">
      <section className="relative overflow-hidden bg-ink px-7 py-10 text-white lg:px-10 lg:py-14">
        <div className="absolute -right-24 -top-24 h-64 w-64 rounded-full border border-white/10" aria-hidden />
        <div className="absolute -right-8 -top-8 h-32 w-32 rounded-full border border-white/10" aria-hidden />
        <Link href="/" className="inline-flex items-center gap-2 text-white">
          <span className="flex h-8 w-8 items-center justify-center rounded-full border border-white/30 text-xs font-semibold">DL</span>
          <span className="font-serif text-lg font-semibold">DealLens</span>
        </Link>
        <div className="relative mt-16 lg:mt-28">
          <p className="text-2xs font-semibold uppercase tracking-eyebrow text-white/50">Governed investing workflow</p>
          <h2 className="mt-3 max-w-md font-serif text-3xl font-semibold leading-tight text-white">One authenticated record from diligence through committee.</h2>
          <ul className="mt-7 space-y-3 text-xs leading-relaxed text-white/65">
            <li className="flex gap-3"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-white/50" />Tenant-scoped workspaces and portfolio reporting</li>
            <li className="flex gap-3"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-white/50" />Revocable, time-limited opaque sessions</li>
            <li className="flex gap-3"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-white/50" />Owner-controlled data and external-model policy</li>
          </ul>
        </div>
      </section>
      <section className="flex items-center px-6 py-10 sm:px-10 lg:px-14">
        <div className="w-full">
          <p className="eyebrow">{eyebrow}</p>
          <h1 className="mt-2 font-serif text-3xl font-semibold text-ink">{title}</h1>
          <p className="mt-2 max-w-lg text-sm leading-relaxed text-muted">{subtitle}</p>
          <div className="mt-7">{children}</div>
          <div className="mt-6 border-t border-line pt-5 text-xs text-muted">{footer}</div>
        </div>
      </section>
    </div>
  );
}
