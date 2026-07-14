import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { ExampleDealButton } from "@/components/ExampleDealButton";
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
        <div className="grid gap-4 md:grid-cols-2">
          <Card eyebrow="Public company" title="Research any SEC registrant">
            <p className="text-sm leading-relaxed text-muted">
              Search for a company by name or ticker and watch the workspace build itself live
              from SEC EDGAR — filings, XBRL financials, risk findings, forensics, and a draft
              IC memo where every material claim cites its source.
            </p>
            <div className="mt-4">
              <Button href="/workspaces/new">Start with a public company</Button>
            </div>
          </Card>
          <Card eyebrow="Private target" title="Walk the underwriting workflow">
            <p className="text-sm leading-relaxed text-muted">
              Load a fully fictional example deal — management financials, a small data room,
              and proposed QoE adjustments — imported through the same governed pipeline. Then
              approve the add-backs, underwrite cases, and assemble an IC packet yourself.
            </p>
            <div className="mt-4">
              <ExampleDealButton variant="primary" />
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
