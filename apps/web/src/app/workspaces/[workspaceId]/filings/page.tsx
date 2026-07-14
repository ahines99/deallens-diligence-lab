import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { Button } from "@/components/ui/Button";
import { FilingTable } from "@/components/FilingTable";
import { FilingWatchPanel } from "@/components/FilingWatchPanel";
import type { Filing, FilingWatch } from "@/lib/types";

export default async function FilingsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let filings: Filing[] | null = null;
  let error: string | null = null;
  try {
    filings = await api.getFilings(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load filings.";
  }

  // Best-effort live filing-watch (hits SEC); never block the filings list.
  const watch: FilingWatch | null = error ? null : await api.getFilingWatch(id).catch(() => null);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Company"
        title="SEC filings"
        subtitle="Real SEC filings (10-K / 10-Q / 8-K) pulled from EDGAR for this company."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : filings && filings.length > 0 ? (
        <>
          <Callout tone="info" title="Live SEC EDGAR">
            These filings were pulled from SEC EDGAR. The latest 10-K's Item 1A / MD&A sections are
            parsed and chunked for risk extraction — the section count reflects that parse.
          </Callout>
          <FilingWatchPanel workspaceId={id} initial={watch} />
          <Card>
            <FilingTable filings={filings} />
          </Card>
        </>
      ) : (
        <EmptyState
          title="No filings yet"
          description="No filings ingested yet. Workspaces created with a ticker pull filings automatically from SEC EDGAR."
          action={
            <Button href={`/workspaces/${id}`} variant="secondary">
              Back to overview
            </Button>
          }
        />
      )}
    </div>
  );
}
