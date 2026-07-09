import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { SourceCitation } from "@/components/SourceCitation";
import { formatPct, titleCase, SEVERITY_ORDER } from "@/lib/formatting";
import type { Priority, RiskFinding, Severity } from "@/lib/types";

// Severity pill: matches the Badge look (rounded-full, ring) but adds a distinct
// dark-red treatment for `critical`, which the shared Badge tones don't cover.
const SEVERITY_PILL: Record<Severity, string> = {
  low: "bg-green-50 text-green-700 ring-green-200",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  high: "bg-red-50 text-red-700 ring-red-200",
  critical: "bg-red-700 text-white ring-red-800",
};

// Left accent border so severity is scannable down the column of cards.
const SEVERITY_ACCENT: Record<Severity, string> = {
  low: "border-l-green-400",
  medium: "border-l-amber-400",
  high: "border-l-red-500",
  critical: "border-l-red-700",
};

const PRIORITY_TONE: Record<Priority, BadgeTone> = {
  low: "slate",
  medium: "amber",
  high: "red",
};

function SeverityPill({ severity, score }: { severity: Severity; score: number }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${SEVERITY_PILL[severity]}`}
    >
      {titleCase(severity)}
      <span className="tabular-nums opacity-80">· {score}/10</span>
    </span>
  );
}

export function RiskMatrix({
  risks,
  workspaceId,
}: {
  risks: RiskFinding[];
  workspaceId: string;
}) {
  const sorted = [...risks].sort(
    (a, b) =>
      b.severity_score - a.severity_score ||
      (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99) ||
      a.title.localeCompare(b.title),
  );

  return (
    <div className="space-y-3">
      {sorted.map((r) => (
        <div
          key={r.id}
          className={`rounded-lg border border-slate-200 border-l-4 bg-white p-4 shadow-sm ${SEVERITY_ACCENT[r.severity]}`}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {r.risk_category_label}
              </div>
              <h3 className="mt-0.5 text-sm font-semibold text-slate-900">{r.title}</h3>
            </div>
            <SeverityPill severity={r.severity} score={r.severity_score} />
          </div>

          <p className="mt-2 text-sm leading-relaxed text-slate-600">{r.finding}</p>

          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5">
              <span className="text-slate-400">Likelihood</span>
              <Badge tone={PRIORITY_TONE[r.likelihood]}>{titleCase(r.likelihood)}</Badge>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-slate-400">Confidence</span>
              <span className="font-medium tabular-nums text-slate-700">
                {formatPct(r.confidence)}
              </span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-slate-400">Owner</span>
              <span className="font-medium text-slate-700">{titleCase(r.workstream_owner)}</span>
            </span>
            {r.evidence_ref && (
              <span className="inline-flex items-center gap-1.5">
                <span className="text-slate-400">Evidence</span>
                <SourceCitation evidenceRef={r.evidence_ref} workspaceId={workspaceId} />
              </span>
            )}
          </div>

          {r.follow_up_question && (
            <div className="mt-3 rounded-md border border-slate-100 bg-slate-50 px-3 py-2 text-sm text-slate-700">
              <span className="font-medium text-slate-500">Follow-up: </span>
              {r.follow_up_question}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default RiskMatrix;
