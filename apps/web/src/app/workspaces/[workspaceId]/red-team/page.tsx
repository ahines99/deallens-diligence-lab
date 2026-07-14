import { api, ApiError } from "@/lib/serverApi";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { GenerateButton } from "@/components/GenerateButton";
import { RedTeamViewer } from "@/components/RedTeamViewer";
import type { RedTeam } from "@/lib/types";

export default async function RedTeamPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;

  let redTeam: RedTeam | null = null;
  let error: string | null = null;
  try {
    redTeam = await api.getRedTeam(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load the red-team pack.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Deliverable"
        title="Red-team / bear case"
        subtitle="An adversarial bear case that stress-tests the thesis, flags unsupported claims, and finds evidence gaps."
        actions={
          <GenerateButton
            kind="red-team"
            workspaceId={id}
            label={redTeam ? "Re-run red-team" : "Run red-team"}
            variant={redTeam ? "secondary" : "primary"}
          />
        }
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh
          this page.
        </Callout>
      ) : redTeam ? (
        <RedTeamViewer redTeam={redTeam} workspaceId={id} />
      ) : (
        <EmptyState
          title="No red-team pack yet"
          description="Run the red-team to generate the strongest bear case against this deal, a list of thinly-supported claims, and the highest-priority questions to close before conviction."
          action={<GenerateButton kind="red-team" workspaceId={id} label="Run red-team" />}
        />
      )}
    </div>
  );
}
