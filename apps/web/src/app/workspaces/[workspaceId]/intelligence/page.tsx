import { loadWorkspaceDeal } from "@/lib/serverApi";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { IntelligenceWorkbench } from "@/components/workbench/IntelligenceWorkbench";

export default async function IntelligencePage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const deal = await loadWorkspaceDeal(workspaceId);
  return <div className="space-y-6"><PageHeader eyebrow="Evidence intelligence" title="Deal-room Q&A, extraction & contradictions" subtitle="Interrogate immutable source versions, review structured claims, and compare documents without allowing unsupported statements into the underwriting record." />{deal.unavailable && <Callout tone="warning" title="Deal lookup unavailable">The linked pipeline deal could not be loaded from the API. This is a data outage, not a missing link — retry once the service is reachable.</Callout>}{deal.data ? <IntelligenceWorkbench deal={deal.data} /> : !deal.unavailable && <Callout tone="muted" title="Connect a pipeline deal"><div className="flex flex-wrap items-center justify-between gap-3"><span>Deal-room intelligence is deal-scoped so its documents, claims, reviews, and audit events remain tenant isolated.</span><Button href="/pipeline" variant="secondary">Open pipeline</Button></div></Callout>}</div>;
}
