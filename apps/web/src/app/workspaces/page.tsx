import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { WorkspaceCard } from "@/components/WorkspaceCard";
import type { Workspace } from "@/lib/types";

export default async function WorkspacesPage() {
  let workspaces: Workspace[] | null = null;
  let error: string | null = null;

  try {
    workspaces = await api.listWorkspaces();
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load workspaces.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Portfolio"
        title="Workspaces"
        subtitle="Each workspace is one diligence engagement — a target, its evidence, and the artifacts you generate."
        actions={<Button href="/workspaces/new">New workspace</Button>}
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh
          this page.
        </Callout>
      ) : workspaces && workspaces.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {workspaces.map((w) => (
            <WorkspaceCard key={w.id} workspace={w} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No workspaces yet"
          description="Create your first diligence workspace by entering a public-company ticker (e.g. MSFT, NVDA, CRWD) — it pulls real SEC filings and financials."
          action={<Button href="/workspaces/new">New workspace</Button>}
        />
      )}
    </div>
  );
}
