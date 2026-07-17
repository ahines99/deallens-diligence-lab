import { api, loadOrUnavailable, loadWorkspaceDeal } from "@/lib/serverApi";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { ICWorkbench } from "@/components/workbench/ICWorkbench";

export default async function ICPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const [deal, cases, evidence] = await Promise.all([
    loadWorkspaceDeal(workspaceId),
    loadOrUnavailable(api.getUnderwritingCases(workspaceId), []),
    loadOrUnavailable(api.getEvidence(workspaceId), []),
  ]);
  const unavailable = deal.unavailable || cases.unavailable || evidence.unavailable;
  return <div className="space-y-6"><PageHeader eyebrow="Investment committee" title="IC readiness, frozen packets & decisions" subtitle="Compose a versioned recommendation from exact model cases and evidence, clear readiness controls, freeze the submission, resolve review comments, and preserve the committee decision."/>{unavailable && <Callout tone="warning" title="IC inputs unavailable">Some IC inputs could not be loaded from the API. This is a data outage, not an empty state — deals, cases, or evidence that already exist are simply not shown. Retry once the service is reachable.</Callout>}{deal.data ? <ICWorkbench deal={deal.data} cases={cases.data} evidence={evidence.data}/> : !deal.unavailable && <Callout tone="muted" title="Connect a pipeline deal"><div className="flex flex-wrap items-center justify-between gap-3"><span>IC governance is deal-scoped so approvals, conditions, exports, and audit history remain attributable.</span><Button href="/pipeline" variant="secondary">Open pipeline</Button></div></Callout>}</div>;
}
