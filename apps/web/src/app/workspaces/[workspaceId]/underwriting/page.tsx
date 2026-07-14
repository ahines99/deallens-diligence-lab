import { api } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Callout } from "@/components/ui/Callout";
import { UnderwritingWorkbench } from "@/components/workbench/UnderwritingWorkbench";

export default async function UnderwritingPage({ params }: { params: Promise<{ workspaceId: string }> }) {
  const { workspaceId } = await params;
  const cases = await api.getUnderwritingCases(workspaceId).catch(() => []);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Investment underwriting" title="Operating model, LBO & debt workbench" subtitle="Run an integrated five-year model across base, upside, and downside cases. Each saved case is immutable, reproducible, and independently reviewable." />
      <Callout tone="info" title="Model convention">Amounts use the case currency and rates use decimals in the calculation engine. The interface translates percentages for entry. Forecasts are monthly for 24 months and annual for years three through five.</Callout>
      <UnderwritingWorkbench workspaceId={workspaceId} cases={cases} />
    </div>
  );
}
