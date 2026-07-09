import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
  className = "",
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`flex flex-col items-center rounded-md border border-dashed border-line-strong bg-panel px-6 py-12 text-center ${className}`}
    >
      <h3 className="font-serif text-lg font-semibold text-ink">{title}</h3>
      {description && (
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted">{description}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
