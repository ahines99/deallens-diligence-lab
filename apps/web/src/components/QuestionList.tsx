import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { SourceCitation } from "@/components/SourceCitation";
import { titleCase } from "@/lib/formatting";
import type { DiligenceQuestion, Priority, Workstream } from "@/lib/types";

const PRIORITY_TONE: Record<Priority, BadgeTone> = {
  high: "red",
  medium: "amber",
  low: "slate",
};

const PRIORITY_ORDER: Record<Priority, number> = {
  high: 0,
  medium: 1,
  low: 2,
};

interface Group {
  workstream: Workstream;
  label: string;
  items: DiligenceQuestion[];
}

export function QuestionList({
  questions,
  workspaceId,
}: {
  questions: DiligenceQuestion[];
  workspaceId: string;
}) {
  // Group by workstream, preserving first-seen order of the workstreams.
  const groups: Group[] = [];
  const byWorkstream = new Map<Workstream, Group>();
  for (const q of questions) {
    let g = byWorkstream.get(q.workstream);
    if (!g) {
      g = { workstream: q.workstream, label: q.workstream_label, items: [] };
      byWorkstream.set(q.workstream, g);
      groups.push(g);
    }
    g.items.push(q);
  }

  // Within each group, most urgent questions first.
  for (const g of groups) {
    g.items.sort(
      (a, b) => (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99),
    );
  }

  return (
    <div className="space-y-8">
      {groups.map((g) => (
        <section key={g.workstream}>
          <div className="mb-3 flex items-center justify-between gap-3 border-b border-slate-200 pb-2">
            <h2 className="text-sm font-semibold text-slate-900">{g.label}</h2>
            <span className="text-xs text-slate-400">
              {g.items.length} question{g.items.length === 1 ? "" : "s"}
            </span>
          </div>

          <ul className="space-y-3">
            {g.items.map((q) => (
              <li
                key={q.id}
                className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-medium leading-relaxed text-slate-900">
                    {q.question}
                  </p>
                  <Badge tone={PRIORITY_TONE[q.priority]}>{titleCase(q.priority)}</Badge>
                </div>
                {q.rationale && (
                  <p className="mt-1.5 text-sm leading-relaxed text-slate-500">{q.rationale}</p>
                )}
                {q.evidence_ref && (
                  <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
                    <span className="text-slate-400">Evidence</span>
                    <SourceCitation evidenceRef={q.evidence_ref} workspaceId={workspaceId} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export default QuestionList;
