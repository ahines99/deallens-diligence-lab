import { api, loadOrUnavailable } from "@/lib/serverApi";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { StressWorkbench } from "@/components/workbench/StressWorkbench";

export default async function StressPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const { data: cases, unavailable } = await loadOrUnavailable(api.getUnderwritingCases(workspaceId), []);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Downside protection" title="Valuation, working capital & stress testing" subtitle="Triangulate enterprise value, quantify the normalized working-capital peg, inspect two-variable sensitivities, and solve directly for return or liquidity break points." />
      {unavailable && <Callout tone="warning" title="Saved cases unavailable">Saved underwriting cases could not be loaded from the API. This is a data outage, not an empty case list — retry once the service is reachable.</Callout>}
      <StressWorkbench workspaceId={workspaceId} cases={cases} />
    </div>
  );
}
