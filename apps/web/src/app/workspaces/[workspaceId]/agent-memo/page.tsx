import { PageHeader } from "@/components/ui/PageHeader";
import { AgentMemoPanel } from "@/components/workbench/AgentMemoPanel";

export default async function AgentMemoPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Agentic diligence"
        title="Agent-drafted IC memo"
        subtitle="The agent drafts one memo section at a time from governed tool results, and every section passes the fail-closed grounding gate independently — a withheld section serves no text while its siblings survive. You accept or reject each section; only accepted sections enter the assembled draft, and every draft state is sealed append-only."
      />
      <AgentMemoPanel workspaceId={workspaceId} />
    </div>
  );
}
