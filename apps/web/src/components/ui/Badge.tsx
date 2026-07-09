import type { ReactNode } from "react";

export type BadgeTone =
  | "neutral"
  | "green"
  | "amber"
  | "red"
  | "critical"
  | "indigo"
  | "slate"
  | "gold";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-sunken text-body ring-line",
  green: "bg-[#e8f1ec] text-severity-low ring-[#cfe3d7]",
  amber: "bg-[#f6efe0] text-severity-medium ring-[#e7d9bd]",
  red: "bg-[#f6ebe7] text-severity-high ring-[#e8d3cb]",
  critical: "bg-[#f3e4e4] text-severity-critical ring-[#e3c9c9]",
  indigo: "bg-accent-soft text-accent ring-[#cfe0ee]",
  slate: "bg-panel2 text-muted ring-line",
  gold: "bg-gold-soft text-gold ring-[#e6d6b6]",
};

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-2xs font-semibold uppercase leading-none tracking-wide ring-1 ring-inset ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
