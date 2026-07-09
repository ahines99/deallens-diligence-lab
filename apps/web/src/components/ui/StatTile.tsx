import type { ReactNode } from "react";

export type StatTone =
  | "default"
  | "positive"
  | "negative"
  | "accent"
  // legacy semantic aliases (mapped below)
  | "neutral"
  | "slate"
  | "green"
  | "red"
  | "amber"
  | "indigo";

const VALUE_TONE: Record<StatTone, string> = {
  default: "text-ink",
  positive: "text-severity-low",
  negative: "text-negative",
  accent: "text-accent",
  neutral: "text-ink",
  slate: "text-ink",
  green: "text-severity-low",
  red: "text-negative",
  amber: "text-severity-medium",
  indigo: "text-accent",
};

export function StatTile({
  label,
  value,
  sub,
  tone = "default",
  className = "",
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: StatTone;
  className?: string;
}) {
  return (
    <div className={`min-w-0 ${className}`}>
      <div className="text-2xs font-semibold uppercase tracking-eyebrow text-muted">{label}</div>
      <div className={`mt-1 font-sans text-[1.6rem] font-semibold leading-none tabular-nums ${VALUE_TONE[tone]}`}>
        {value}
      </div>
      {sub && <div className="mt-1.5 text-xs leading-snug text-muted">{sub}</div>}
    </div>
  );
}
