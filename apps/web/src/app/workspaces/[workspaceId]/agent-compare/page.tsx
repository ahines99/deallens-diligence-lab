import { PageHeader } from "@/components/ui/PageHeader";
import { AgentComparePanel } from "@/components/workbench/AgentComparePanel";

export default async function AgentComparePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Agentic diligence"
        title="Comparative agent run"
        subtitle="One objective across this workspace and up to three comp workspaces. Every workspace runs its own harness-scoped governed agent, every workspace must consent, and the merged answer is a deterministic concatenation of the individually grounded, per-workspace-labeled answers — sealed as an append-only artifact on this workspace."
      />
      <AgentComparePanel workspaceId={workspaceId} />
    </div>
  );
}
