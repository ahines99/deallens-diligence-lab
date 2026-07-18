import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/serverApi";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import type { SharedWorkspaceSnapshot } from "@/lib/types";

const SEVERITY_TONE: Record<string, BadgeTone> = {
  critical: "critical",
  high: "red",
  medium: "amber",
  low: "green",
};

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * Persistent tiled watermark over the whole shared render (G76). The text is SERVER-composed
 * (`snapshot.watermark` IS the payload field) so a client cannot drop it by ignoring a boolean
 * flag. It is a provenance deterrent — who shared, which link, when — not DRM: a determined
 * viewer can always screenshot around any client-side overlay.
 */
function WatermarkOverlay({ text }: { text: string }) {
  const tile =
    `<svg xmlns='http://www.w3.org/2000/svg' width='480' height='260'>` +
    `<text x='240' y='130' text-anchor='middle' transform='rotate(-24 240 130)' ` +
    `font-family='Georgia, serif' font-size='13' fill='rgba(10,42,67,0.07)'>` +
    `${escapeXml(text)}</text></svg>`;
  // encodeURIComponent leaves ' ( ) raw, which are unsafe inside a CSS url() — escape them too.
  const encoded = encodeURIComponent(tile)
    .replace(/'/g, "%27")
    .replace(/\(/g, "%28")
    .replace(/\)/g, "%29");
  return (
    <div
      aria-hidden
      data-testid="share-watermark-overlay"
      className="pointer-events-none fixed inset-0 z-40"
      style={{ backgroundImage: `url("data:image/svg+xml;utf8,${encoded}")` }}
    />
  );
}

export default async function SharedSnapshotPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  let snapshot: SharedWorkspaceSnapshot;
  try {
    snapshot = await api.getSharedSnapshot(token);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    if (e instanceof ApiError && e.status === 410) {
      return (
        <Callout tone="warning" title="Share link no longer active">
          This share link has been revoked or has expired. Ask the person who shared it for a
          new link.
        </Callout>
      );
    }
    return (
      <Callout tone="warning" title="Can't reach the API">
        {e instanceof ApiError ? e.message : "Failed to load the shared snapshot."} Try again
        shortly.
      </Callout>
    );
  }

  const { workspace, target, risks, watermark } = snapshot;

  return (
    <div className="relative space-y-6">
      <WatermarkOverlay text={watermark} />

      {/* Persistent visible banner — the same server-composed watermark line, always legible. */}
      <div className="sticky top-14 z-30 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-line bg-gold-soft px-4 py-2 shadow-panel">
        <span className="text-2xs font-semibold uppercase tracking-eyebrow text-gold">
          {watermark}
        </span>
      </div>

      <header>
        <p className="eyebrow">Shared snapshot</p>
        <h1 className="mt-1 font-serif text-2xl font-semibold tracking-tight">
          {workspace.name}
        </h1>
        {workspace.investment_question && (
          <p className="mt-1 max-w-measure text-sm text-muted">{workspace.investment_question}</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {workspace.deal_type && <Badge tone="indigo">{workspace.deal_type}</Badge>}
          {workspace.status && <Badge>{workspace.status}</Badge>}
          <Badge tone="slate">read-only</Badge>
        </div>
      </header>

      {target && (
        <section className="rounded-md border border-line bg-panel px-5 py-4 shadow-panel">
          <p className="eyebrow">Target</p>
          <p className="mt-1 font-serif text-lg font-semibold text-ink">
            {target.name}
            {target.ticker && (
              <span className="ml-2 font-sans text-xs font-semibold text-muted">
                {target.ticker}
              </span>
            )}
          </p>
          {target.sector && <p className="text-xs text-muted">{target.sector}</p>}
          {target.description && (
            <p className="mt-2 max-w-measure text-sm leading-relaxed">{target.description}</p>
          )}
        </section>
      )}

      <section className="rounded-md border border-line bg-panel px-5 py-4 shadow-panel">
        <p className="eyebrow">Risk findings ({risks.length})</p>
        {risks.length === 0 ? (
          <p className="mt-2 text-sm text-muted">No risk findings in this snapshot.</p>
        ) : (
          <ul className="mt-2 divide-y divide-line">
            {risks.map((risk, index) => (
              <li key={index} className="flex items-start justify-between gap-3 py-2.5">
                <div>
                  <p className="text-sm font-medium text-ink">{risk.title}</p>
                  <p className="text-2xs uppercase tracking-wide text-muted">
                    {risk.category_label}
                  </p>
                </div>
                <Badge tone={SEVERITY_TONE[risk.severity] ?? "neutral"}>{risk.severity}</Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Callout tone="muted" title="About this page">
        {snapshot.disclaimer}
      </Callout>
    </div>
  );
}
