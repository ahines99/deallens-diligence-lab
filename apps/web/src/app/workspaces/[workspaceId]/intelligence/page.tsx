import { api } from "@/lib/serverApi";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { IntelligenceWorkbench } from "@/components/workbench/IntelligenceWorkbench";

export default async function IntelligencePage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const deal = await api.getWorkspaceDeal(workspaceId).catch(() => null);
  return <div className="space-y-6"><PageHeader eyebrow="Evidence intelligence" title="Deal-room Q&A, extraction & contradictions" subtitle="Interrogate immutable source versions, review structured claims, and compare documents without allowing unsupported statements into the underwriting record." />{deal ? <IntelligenceWorkbench deal={deal} /> : <Callout tone="muted" title="Connect a pipeline deal"><div className="flex flex-wrap items-center justify-between gap-3"><span>Deal-room intelligence is deal-scoped so its documents, claims, reviews, and audit events remain tenant isolated.</span><Button href="/pipeline" variant="secondary">Open pipeline</Button></div></Callout>}</div>;
}
