import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { SourceCitation } from "@/components/SourceCitation";
import { formatPct, titleCase, SEVERITY_ORDER } from "@/lib/formatting";
import type { Priority, RiskFinding, Severity } from "@/lib/types";

// Severity → shared Badge tone (low→green, medium→amber, high→red, critical→critical).
const SEVERITY_TONE: Record<Severity, BadgeTone> = {
  low: "green",
  medium: "amber",
  high: "red",
  critical: "critical",
};

// Left hairline accent so severity is scannable down the column of cards.
const SEVERITY_ACCENT: Record<Severity, string> = {
  low: "border-l-severity-low",
  medium: "border-l-severity-medium",
  high: "border-l-severity-high",
  critical: "border-l-severity-critical",
};

const PRIORITY_TONE: Record<Priority, BadgeTone> = {
  low: "slate",
  medium: "amber",
  high: "red",
};

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
          className={`rounded-md border border-l-2 border-line bg-panel p-4 shadow-panel ${SEVERITY_ACCENT[r.severity]}`}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <Eyebrow>{r.risk_category_label}</Eyebrow>
              <h3 className="mt-0.5 text-sm font-semibold text-ink">{r.title}</h3>
            </div>
            <Badge tone={SEVERITY_TONE[r.severity]}>
              {titleCase(r.severity)}
              <span className="tabular-nums opacity-80">· {r.severity_score}/10</span>
            </Badge>
          </div>

          <p className="mt-2 text-sm leading-relaxed text-body">{r.finding}</p>

          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5">
              <span className="text-faint">Likelihood</span>
              <Badge tone={PRIORITY_TONE[r.likelihood]}>{titleCase(r.likelihood)}</Badge>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-faint">Confidence</span>
              <span className="font-medium tabular-nums text-body">{formatPct(r.confidence)}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-faint">Owner</span>
              <span className="font-medium text-body">{titleCase(r.workstream_owner)}</span>
            </span>
            {r.evidence_ref && (
              <span className="inline-flex items-center gap-1.5">
                <span className="text-faint">Evidence</span>
                <SourceCitation evidenceRef={r.evidence_ref} workspaceId={workspaceId} />
              </span>
            )}
          </div>

          {r.follow_up_question && (
            <div className="mt-3 rounded-md border border-line-faint bg-panel2 px-3 py-2 text-sm text-body">
              <span className="font-medium text-muted">Follow-up: </span>
              {r.follow_up_question}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default RiskMatrix;
