import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { EventTimeline } from "@/components/EventTimeline";
import type { EventTimeline as EventTimelineData } from "@/lib/types";

export default async function EventsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let data: EventTimelineData | null = null;
  let error: string | null = null;
  try {
    data = await api.getEvents(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load filing events.";
  }

  const sigCount = data?.events.filter((e) => e.significant).length ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Company"
        title="Filing events"
        subtitle="A timeline of recent SEC filings with 8-K item codes decoded. Material events (restatements, auditor changes, cyber incidents) are flagged."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : data && data.events.length > 0 ? (
        <Card
          title="Recent filings"
          subtitle={`${data.events.length} events${sigCount > 0 ? ` · ${sigCount} significant` : ""}`}
        >
          <EventTimeline data={data} />
        </Card>
      ) : (
        <EmptyState
          title="No filing events available"
          description="Filing events are read live from SEC EDGAR submissions. Ingest a public company with a CIK to populate the timeline."
        />
      )}
    </div>
  );
}
