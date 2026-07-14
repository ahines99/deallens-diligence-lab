import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { ValuationView } from "@/components/ValuationView";
import type { Valuation } from "@/lib/types";

export default async function ValuationPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let data: Valuation | null = null;
  let error: string | null = null;
  try {
    data = await api.getValuation(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load valuation.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Valuation & LBO"
        subtitle="WACC, a DCF-lite enterprise value, and an interactive LBO returns model. Every input is a labeled assumption — decision support, not a fairness opinion."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : data ? (
        <ValuationView data={data} workspaceId={id} />
      ) : (
        <EmptyState
          title="No valuation available"
          description="Valuation is derived from the target's stored XBRL financials plus the live FRED 10-year yield. Ingest a public company to populate WACC, DCF and the LBO model."
        />
      )}
    </div>
  );
}
