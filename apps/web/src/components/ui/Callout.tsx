import type { ReactNode } from "react";

export type CalloutTone = "info" | "warning" | "synthetic";

const TONES: Record<CalloutTone, { wrap: string; accent: string; icon: string }> = {
  info: {
    wrap: "border-brand-100 bg-brand-50 text-slate-700",
    accent: "text-brand-700",
    icon: "ⓘ", // circled i
  },
  warning: {
    wrap: "border-amber-200 bg-amber-50 text-amber-900",
    accent: "text-amber-800",
    icon: "⚠", // warning sign
  },
  synthetic: {
    wrap: "border-amber-200 bg-amber-50 text-slate-700",
    accent: "text-brand-700",
    icon: "⚠", // warning sign, indigo accent for the synthetic-data notice
  },
};

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: CalloutTone;
  title?: ReactNode;
  children?: ReactNode;
}) {
  const t = TONES[tone];
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${t.wrap}`}>
      <div className="flex gap-3">
        <span className={`mt-0.5 select-none text-base leading-none ${t.accent}`} aria-hidden>
          {t.icon}
        </span>
        <div className="min-w-0 flex-1">
          {title && <div className={`font-semibold ${t.accent}`}>{title}</div>}
          {children && <div className={title ? "mt-1" : ""}>{children}</div>}
        </div>
      </div>
    </div>
  );
}

export default Callout;
