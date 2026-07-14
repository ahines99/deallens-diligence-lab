import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { NewsFeed } from "@/components/NewsFeed";
import type { NewsSignals } from "@/lib/types";

export default async function NewsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let data: NewsSignals | null = null;
  let error: string | null = null;
  try {
    data = await api.getNews(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load news signals.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="News signals"
        subtitle="Recent English-language coverage from GDELT for market context. Unverified media — never used as evidence."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : data ? (
        <Card title="Recent coverage" subtitle={data.query ? `Query: ${data.query}` : undefined}>
          <NewsFeed data={data} />
        </Card>
      ) : (
        <EmptyState
          title="No news signals available"
          description="News is pulled live from GDELT for the target company name. Ingest a company to populate this feed."
        />
      )}
    </div>
  );
}
