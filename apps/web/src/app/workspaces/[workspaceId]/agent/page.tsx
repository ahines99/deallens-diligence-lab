import { PageHeader } from "@/components/ui/PageHeader";
import { AgentConsole } from "@/components/workbench/AgentConsole";

export default async function AgentPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Agentic diligence"
        title="Diligence agent"
        subtitle="A budget-capped tool loop over this workspace's governed, read-only tools. Every run is sealed as an append-only artifact, and the final answer is served only when every number and evidence reference traces to a tool result."
      />
      <AgentConsole workspaceId={workspaceId} />
    </div>
  );
}
