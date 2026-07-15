import { api } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";

const STATUS_TONE: Record<string, BadgeTone> = {
  available: "green",
  partial: "amber",
  unavailable: "red",
};

export default async function SignalsOverviewPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;
  const overview = await api.getSignalsOverview(id).catch(() => null);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Signals"
        title="Consolidated signals overview"
        subtitle="Filing events, insider activity, news, and thematic scans in one view — each with its own explicit source status."
        actions={
          overview ? (
            <Badge tone={STATUS_TONE[overview.overall_status] ?? "slate"}>
              {overview.overall_status}
            </Badge>
          ) : undefined
        }
      />
      {!overview ? (
        <EmptyState
          title="No signals yet"
          description="Ingest a public target to populate filing events, insider activity, news, and themes."
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {overview.sections.map((section) => (
            <Card
              key={section.kind}
              eyebrow={section.kind}
              title={section.summary}
              right={
                <Badge tone={STATUS_TONE[section.source_status] ?? "slate"}>
                  {section.source_status}
                </Badge>
              }
            >
              {section.source_status === "unavailable" ? (
                <Callout tone="warning">
                  {section.source_error ?? "This source is currently unavailable."}
                </Callout>
              ) : section.items.length === 0 ? (
                <p className="text-sm text-muted">No recent items.</p>
              ) : (
                <p className="text-sm text-muted">{section.items.length} recent item(s).</p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
