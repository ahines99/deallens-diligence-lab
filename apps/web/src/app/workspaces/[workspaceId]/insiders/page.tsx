import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { InsiderView } from "@/components/InsiderView";
import type { InsiderActivity } from "@/lib/types";

export default async function InsidersPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let data: InsiderActivity | null = null;
  let error: string | null = null;
  try {
    data = await api.getInsiders(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load insider activity.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Insider activity"
        subtitle="Recent open-market buys and sells by officers and directors, parsed from SEC Form 4 filings."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : data && data.transactions.length > 0 ? (
        <InsiderView data={data} />
      ) : (
        <EmptyState
          title="No insider transactions found"
          description="Insider activity is read live from SEC Form 4 filings for the target's CIK. Ingest a public company, or there may simply be no Form 4s in the recent window."
        />
      )}
    </div>
  );
}
