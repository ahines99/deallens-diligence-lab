import type { ReactNode } from "react";

export type StatTone = "neutral" | "green" | "amber" | "red" | "indigo";

const VALUE_TONE: Record<StatTone, string> = {
  neutral: "text-slate-900",
  green: "text-green-700",
  amber: "text-amber-700",
  red: "text-red-700",
  indigo: "text-brand-700",
};

export function StatTile({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: StatTone;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${VALUE_TONE[tone]}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export default StatTile;
