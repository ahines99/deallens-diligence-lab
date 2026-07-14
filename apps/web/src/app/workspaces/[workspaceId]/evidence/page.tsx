import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { GenerateButton } from "@/components/GenerateButton";
import { EvidenceTable } from "@/components/EvidenceTable";
import type { Evidence } from "@/lib/types";

export default async function EvidencePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let evidence: Evidence[] | null = null;
  let error: string | null = null;
  try {
    evidence = await api.getEvidence(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load evidence.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Deliverable"
        title="Evidence & audit trail"
        subtitle="The audit trail behind the pack — every claim, its source, claim type, and confidence."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh
          this page.
        </Callout>
      ) : evidence && evidence.length > 0 ? (
        <EvidenceTable evidence={evidence} workspaceId={id} />
      ) : (
        <EmptyState
          title="No evidence yet"
          description="Evidence rows are created as you generate artifacts. Run the red-flag screen, diligence questions, or IC memo first, then return here to see the full audit trail."
          action={<GenerateButton kind="risks" workspaceId={id} label="Generate red flags" />}
        />
      )}
    </div>
  );
}
