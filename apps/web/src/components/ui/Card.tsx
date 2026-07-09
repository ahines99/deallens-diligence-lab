import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  eyebrow,
  right,
  children,
  className = "",
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  eyebrow?: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const hasHeader = title || eyebrow || right;
  return (
    <section
      className={`overflow-hidden rounded-md border border-line bg-panel shadow-panel ${className}`}
    >
      {hasHeader && (
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-3.5">
          <div className="min-w-0">
            {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
            {title && (
              <h3 className="font-sans text-sm font-semibold leading-tight text-ink">{title}</h3>
            )}
            {subtitle && <p className="mt-1 text-xs leading-snug text-muted">{subtitle}</p>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </div>
      )}
      {children !== undefined && (
        <div className={bodyClassName ?? "px-5 py-4"}>{children}</div>
      )}
    </section>
  );
}
