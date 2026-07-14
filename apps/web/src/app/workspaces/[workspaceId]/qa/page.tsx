import { PageHeader } from "@/components/ui/PageHeader";
import { Callout } from "@/components/ui/Callout";
import { FilingsQAPanel } from "@/components/FilingsQAPanel";

export default async function FilingsQAPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Public research"
        title="Ask the filings"
        subtitle="Question the ingested 10-K directly. Answers are verbatim extracts, each cited back to its section and sec.gov document — when the filings don't contain the evidence, the system says so."
      />
      <FilingsQAPanel workspaceId={id} />
      <Callout tone="info" title="How this stays honest">
        Retrieval is deterministic (BM25 over the ingested filing sections), answers are strictly
        extractive, and unanswerable questions produce an explicit abstention rather than a
        plausible guess. The same abstain-or-cite discipline governs the private data-room Q&A.
      </Callout>
    </div>
  );
}
