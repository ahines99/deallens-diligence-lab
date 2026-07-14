"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const GROUPS: { label: string; items: { label: string; seg: string }[] }[] = [
  {
    label: "Deal",
    items: [
      { label: "Deal cockpit", seg: "" },
      { label: "Data room", seg: "data-room" },
      { label: "QoE bridge", seg: "qoe" },
    ],
  },
  {
    label: "Underwrite",
    items: [
      { label: "Operating model & LBO", seg: "underwriting" },
      { label: "Valuation & stress", seg: "stress" },
    ],
  },
  {
    label: "Execute",
    items: [
      { label: "Workplan & requests", seg: "execution" },
      { label: "Evidence intelligence", seg: "intelligence" },
      { label: "IC readiness & decision", seg: "ic" },
    ],
  },
  {
    label: "Signals",
    items: [
      { label: "Filing events", seg: "events" },
      { label: "Insider activity", seg: "insiders" },
      { label: "News signals", seg: "news" },
      { label: "Macro overlay", seg: "macro" },
      { label: "GovCon exposure", seg: "govcon" },
    ],
  },
  {
    label: "Public research",
    items: [
      { label: "Target profile", seg: "target" },
      { label: "Financial trends", seg: "trends" },
      { label: "SEC filings", seg: "filings" },
      { label: "Ask the filings", seg: "qa" },
      { label: "Comps & benchmark", seg: "comps" },
      { label: "QoE forensics", seg: "forensics" },
      { label: "Red-flag matrix", seg: "risks" },
      { label: "Diligence questions", seg: "questions" },
      { label: "Red-team case", seg: "red-team" },
      { label: "Evidence trail", seg: "evidence" },
      { label: "Public-data valuation", seg: "valuation" },
      { label: "IC memo draft", seg: "memo" },
    ],
  },
];

export function WorkspaceNav({ base }: { base: string }) {
  const pathname = usePathname();
  return (
    <nav className="space-y-5 lg:max-h-[calc(100vh-14rem)] lg:overflow-y-auto lg:pr-1">
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
