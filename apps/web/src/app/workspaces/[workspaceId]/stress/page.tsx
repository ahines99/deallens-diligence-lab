import { api } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { StressWorkbench } from "@/components/workbench/StressWorkbench";

export default async function StressPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const cases = await api.getUnderwritingCases(workspaceId).catch(() => []);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Downside protection" title="Valuation, working capital & stress testing" subtitle="Triangulate enterprise value, quantify the normalized working-capital peg, inspect two-variable sensitivities, and solve directly for return or liquidity break points." />
      <StressWorkbench workspaceId={workspaceId} cases={cases} />
    </div>
  );
}
