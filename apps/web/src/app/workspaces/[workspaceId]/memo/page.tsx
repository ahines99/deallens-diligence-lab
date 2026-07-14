import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { GenerateButton } from "@/components/GenerateButton";
import { MemoViewer } from "@/components/MemoViewer";
import type { Memo, MemoFaithfulnessReport } from "@/lib/types";

const DOCUMENT_LABELS: Record<string, string> = {
  ic_memo: "IC memo",
  bear_case: "Bear case",
  red_team_bear_case: "Red-team bear case",
};

function FaithfulnessPanel({ report }: { report: MemoFaithfulnessReport }) {
  if (report.documents.length === 0) return null;
  return (
    <Card
      eyebrow="Runtime faithfulness report"
      title="Do the memo's citations hold up?"
    >
      <p className="text-sm leading-relaxed text-muted">
        Recomputed live against the workspace&apos;s {report.evidence_ref_count} evidence rows:
        every cited <code className="font-mono text-xs">EV-###</code> must resolve, and numeric
        claims without a citation are surfaced for review rather than assumed fine.
      </p>
      <div className="mt-4 space-y-3">
        {report.documents.map((doc) => (
          <div key={doc.document_type} className="rounded-md border border-line-faint p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-ink">
                {DOCUMENT_LABELS[doc.document_type] ?? doc.document_type}
              </span>
              {doc.fully_resolved ? (
                <Badge tone="green">All {doc.citation_count} citations resolve</Badge>
              ) : (
                <Badge tone="red">{doc.unresolved_refs.length} unresolved citations</Badge>
              )}
              <span className="text-xs text-muted">
                {doc.distinct_refs} distinct refs · {doc.numeric_token_count} numeric tokens ·{" "}
                {doc.uncited_numeric_sentence_count} uncited numeric sentences
              </span>
            </div>
            {doc.unresolved_refs.length > 0 && (
              <p className="mt-2 font-mono text-xs text-negative">
                Unresolved: {doc.unresolved_refs.join(", ")}
              </p>
            )}
            {doc.uncited_numeric_sentences.length > 0 && (
              <ul className="mt-2 space-y-1">
                {doc.uncited_numeric_sentences.slice(0, 3).map((sentence) => (
                  <li key={sentence} className="text-xs leading-relaxed text-muted">
                    ▸ {sentence}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}

export default async function MemoPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let memo: Memo | null = null;
  let error: string | null = null;
  let faithfulness: MemoFaithfulnessReport | null = null;
  try {
    memo = await api.getMemo(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load the IC memo.";
  }
  if (memo) {
    try {
      faithfulness = await api.getMemoFaithfulness(id);
    } catch {
      faithfulness = null; // diagnostics are best-effort; the memo still renders
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Deliverable"
        title="IC memo"
        subtitle="An investment-committee draft synthesized from the target, evidence, and risk work."
        actions={
          <GenerateButton
            kind="memo"
            workspaceId={id}
            label={memo ? "Regenerate memo" : "Generate IC memo"}
            variant={memo ? "secondary" : "primary"}
          />
        }
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh
          this page.
        </Callout>
      ) : memo ? (
        <>
          <Callout tone="info" title="Draft for human review">
            This memo is machine-generated for analyst review. Verify every claim against its evidence
            before relying on it. It is not investment advice.
          </Callout>
          {faithfulness && <FaithfulnessPanel report={faithfulness} />}
          <MemoViewer memo={memo} />
        </>
      ) : (
        <EmptyState
          title="No IC memo yet"
          description="Generate an investment-committee memo that pulls together the diligence plan, financial benchmark, red flags, and evidence into a single narrative."
          action={<GenerateButton kind="memo" workspaceId={id} label="Generate IC memo" />}
        />
      )}
    </div>
  );
}
