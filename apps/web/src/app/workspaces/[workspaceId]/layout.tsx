"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS: { label: string; seg: string }[] = [
  { label: "Overview", seg: "" },
  { label: "Target", seg: "target" },
  { label: "Trends", seg: "trends" },
  { label: "Macro", seg: "macro" },
  { label: "Filings", seg: "filings" },
  { label: "Comps", seg: "comps" },
  { label: "GovCon", seg: "govcon" },
  { label: "Risks", seg: "risks" },
  { label: "Questions", seg: "questions" },
  { label: "IC Memo", seg: "memo" },
  { label: "Red-Team", seg: "red-team" },
  { label: "Evidence", seg: "evidence" },
];

export default function WorkspaceLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: { workspaceId: string };
}) {
  const pathname = usePathname();
  const base = `/workspaces/${params.workspaceId}`;

  return (
    <div className="space-y-6">
      <div className="overflow-x-auto border-b border-slate-200">
        <nav className="flex min-w-max gap-1">
          {TABS.map((t) => {
            const href = t.seg ? `${base}/${t.seg}` : base;
            const active = t.seg ? pathname === href || pathname.startsWith(`${href}/`) : pathname === base;
            return (
              <Link
                key={t.label}
                href={href}
                className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors ${
                  active
                    ? "border-brand-600 font-medium text-brand-700"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                {t.label}
              </Link>
            );
          })}
        </nav>
      </div>
      {children}
    </div>
  );
}
