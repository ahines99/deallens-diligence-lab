import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { GenerateButton } from "@/components/GenerateButton";
import { MemoViewer } from "@/components/MemoViewer";
import type { Memo } from "@/lib/types";

export default async function MemoPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let memo: Memo | null = null;
  let error: string | null = null;
  try {
    memo = await api.getMemo(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load the IC memo.";
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
