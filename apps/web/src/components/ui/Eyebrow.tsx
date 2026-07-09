import type { ReactNode } from "react";

/** Small uppercase kicker label used above section titles. */
export function Eyebrow({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <p className={`eyebrow ${className}`}>{children}</p>;
}
