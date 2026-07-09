import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { ForensicsView } from "@/components/ForensicsView";
import type { Forensics } from "@/lib/types";

export default async function ForensicsPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let data: Forensics | null = null;
  let error: string | null = null;
  try {
    data = await api.getForensics(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load forensic scores.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Quality of earnings & forensics"
        subtitle={
          data?.as_of_year
            ? `Altman Z″, Piotroski F, Beneish M and accruals — FY${data.as_of_year}.`
            : "Bankruptcy, earnings-quality and manipulation screens computed from filed XBRL."
        }
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : data ? (
        <ForensicsView data={data} />
      ) : (
        <EmptyState
          title="No forensic scores available"
          description="Forensic scores are computed from the target's stored SEC XBRL financials. Ingest a public company with multi-year financials to populate this screen."
        />
      )}
    </div>
  );
}
