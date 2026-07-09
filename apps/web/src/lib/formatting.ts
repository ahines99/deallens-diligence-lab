// Deterministic display formatting helpers. No locale surprises in SSR.

export function formatUsd(value: number | null | undefined, opts?: { compact?: boolean }): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const compact = opts?.compact ?? true;
  const abs = Math.abs(value);
  if (compact) {
    if (abs >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`;
    if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
    if (abs >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  }
  return `$${value.toLocaleString("en-US")}`;
}

export function formatPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatMultiple(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}x`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US");
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  // Render as YYYY-MM-DD to stay deterministic across server/client.
  return iso.slice(0, 10);
}

export function titleCase(slug: string): string {
  return slug
    .split(/[_\s-]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

export const DEAL_TYPE_LABELS: Record<string, string> = {
  buyout: "Buyout",
  growth_equity: "Growth Equity",
  private_credit: "Private Credit",
  public_equity: "Public Equity Research",
  govcon: "GovCon Acquisition",
  software_platform: "Software Platform",
};

export const CLAIM_TYPE_LABELS: Record<string, string> = {
  fact: "Fact",
  calculation: "Calculation",
  inference: "Inference",
  assumption: "Assumption",
};
