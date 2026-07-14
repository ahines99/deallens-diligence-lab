import { api } from "@/lib/serverApi";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { ExecutionWorkbench } from "@/components/workbench/ExecutionWorkbench";

export default async function ExecutionPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const deal = await api.getWorkspaceDeal(workspaceId).catch(() => null);
  return <div className="space-y-6"><PageHeader eyebrow="Deal execution" title="Diligence workplan & decision ledger" subtitle="Coordinate workstreams, milestones, tasks, management requests, stage gates, ownership, and the evolving investment thesis in one controlled record." />{deal ? <ExecutionWorkbench workspaceId={workspaceId} initialDeal={deal} /> : <Callout tone="muted" title="Connect this workspace to a pipeline deal"><div className="flex flex-wrap items-center justify-between gap-3"><span>Create or update a pipeline deal with this workspace selected. Deal governance remains tenant- and fund-scoped.</span><Button href="/pipeline" variant="secondary">Open pipeline</Button></div></Callout>}</div>;
}
