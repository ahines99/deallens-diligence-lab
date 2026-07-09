import type { ReactNode } from "react";

export type CalloutTone = "info" | "warning" | "synthetic" | "muted";

const TONES: Record<CalloutTone, { border: string; bg: string; label: string }> = {
  info: { border: "border-l-accent", bg: "bg-panel2", label: "text-accent" },
  warning: { border: "border-l-severity-high", bg: "bg-[#faf1ee]", label: "text-severity-high" },
  synthetic: { border: "border-l-gold", bg: "bg-gold-soft", label: "text-gold" },
  muted: { border: "border-l-line-strong", bg: "bg-panel2", label: "text-muted" },
};

export function Callout({
  tone = "info",
  title,
  children,
  className = "",
}: {
  tone?: CalloutTone;
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const t = TONES[tone];
  return (
    <div className={`rounded-r-md border border-l-2 border-line ${t.border} ${t.bg} px-4 py-3 ${className}`}>
      {title && (
        <p className={`mb-0.5 text-2xs font-semibold uppercase tracking-eyebrow ${t.label}`}>
          {title}
        </p>
      )}
      <div className="text-xs leading-relaxed text-muted">{children}</div>
    </div>
  );
}
