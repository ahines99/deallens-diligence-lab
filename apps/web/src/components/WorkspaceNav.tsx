"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const GROUPS: { label: string; items: { label: string; seg: string }[] }[] = [
  {
    label: "Company",
    items: [
      { label: "Overview", seg: "" },
      { label: "Target", seg: "target" },
      { label: "Trends", seg: "trends" },
      { label: "Macro", seg: "macro" },
      { label: "Filings", seg: "filings" },
      { label: "Events", seg: "events" },
    ],
  },
  {
    label: "Analysis",
    items: [
      { label: "Comps & Benchmark", seg: "comps" },
      { label: "Quality of Earnings", seg: "forensics" },
      { label: "Valuation & LBO", seg: "valuation" },
      { label: "GovCon", seg: "govcon" },
      { label: "Red-Flag Matrix", seg: "risks" },
      { label: "Insiders", seg: "insiders" },
      { label: "News", seg: "news" },
      { label: "Diligence Questions", seg: "questions" },
    ],
  },
  {
    label: "Deliverables",
    items: [
      { label: "IC Memo", seg: "memo" },
      { label: "Red-Team", seg: "red-team" },
      { label: "Evidence", seg: "evidence" },
    ],
  },
];

export function WorkspaceNav({ base }: { base: string }) {
  const pathname = usePathname();
  return (
    <nav className="space-y-5">
      {GROUPS.map((g) => (
        <div key={g.label}>
          <div className="eyebrow mb-1.5 px-2.5 text-faint">{g.label}</div>
          <ul className="space-y-0.5">
            {g.items.map((it) => {
              const href = it.seg ? `${base}/${it.seg}` : base;
              const active = it.seg
                ? pathname === href || pathname.startsWith(`${href}/`)
                : pathname === base;
              return (
                <li key={it.label}>
                  <Link
                    href={href}
                    className={`-ml-px flex items-center border-l-2 py-1.5 pl-2.5 pr-2 text-sm transition-colors ${
                      active
                        ? "border-accent bg-accent-soft font-medium text-accent"
                        : "border-transparent text-body hover:bg-panel2 hover:text-ink"
                    }`}
                  >
                    {it.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
