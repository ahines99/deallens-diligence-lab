import { forwardRef } from "react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

export const controlClass =
  "w-full rounded border border-line-strong bg-panel px-3 py-2 text-sm text-ink shadow-xs placeholder:text-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:bg-panel2 disabled:text-muted";

export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1.5 flex items-center justify-between gap-2 text-2xs font-semibold uppercase tracking-eyebrow text-muted">
        {label}
        {hint && <span className="font-normal normal-case tracking-normal text-faint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput(props, ref) {
    return <input ref={ref} {...props} className={`${controlClass} ${props.className ?? ""}`} />;
  },
);

export function SelectInput(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${controlClass} ${props.className ?? ""}`} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${controlClass} ${props.className ?? ""}`} />;
}

export function MetricStrip({ children, columns = 4 }: { children: ReactNode; columns?: 3 | 4 | 5 | 6 }) {
  const cols = {
    3: "lg:grid-cols-3",
    4: "lg:grid-cols-4",
    5: "lg:grid-cols-5",
    6: "lg:grid-cols-6",
  }[columns];
  return <div className={`grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-3 ${cols}`}>{children}</div>;
}

export function Metric({ label, value, detail, tone = "default" }: { label: ReactNode; value: ReactNode; detail?: ReactNode; tone?: "default" | "positive" | "warning" | "negative" }) {
  const tones = {
    default: "text-ink",
    positive: "text-positive",
    warning: "text-warn",
    negative: "text-negative",
  };
  return (
    <div className="min-w-0 bg-panel px-4 py-3.5">
      <div className="text-2xs font-semibold uppercase tracking-eyebrow text-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${tones[tone]}`}>{value}</div>
      {detail && <div className="mt-1 truncate text-2xs text-faint">{detail}</div>}
    </div>
  );
}

export function SectionTitle({ eyebrow, title, detail, action }: { eyebrow?: ReactNode; title: ReactNode; detail?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line pb-2.5">
      <div>
        {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
        <h2 className="font-sans text-base font-semibold text-ink">{title}</h2>
        {detail && <p className="mt-1 text-xs text-muted">{detail}</p>}
      </div>
      {action}
    </div>
  );
}

export function StatusDot({ status }: { status: string }) {
  const good = ["ready", "approved", "accepted", "complete", "satisfied", "succeeded", "closed"];
  const bad = ["failed", "rejected", "blocked", "declined"];
  const cls = good.includes(status) ? "bg-positive" : bad.includes(status) ? "bg-negative" : "bg-warn";
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${cls}`} aria-hidden />;
}

export function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="text-xs leading-relaxed text-negative">{message}</p>;
}

export function EmptyPanel({ title, body, action }: { title: ReactNode; body: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-line-strong bg-panel2 px-5 py-8 text-center">
      <p className="text-sm font-semibold text-ink">{title}</p>
      <p className="mx-auto mt-1.5 max-w-xl text-xs leading-relaxed text-muted">{body}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
