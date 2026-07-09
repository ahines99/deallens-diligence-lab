import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { GovConView } from "@/components/GovConView";
import { GovConFetchForm } from "@/components/GovConFetchForm";
import type { GovConProfile, Target } from "@/lib/types";

export default async function GovConPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let profile: GovConProfile | null = null;
  let error: string | null = null;
  try {
    profile = await api.getGovCon(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load the federal contract profile.";
  }

  let target: Target | null = null;
  if (!error && !profile) {
    target = await api.getTarget(id).catch(() => null);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Analysis"
        title="Federal contract profile (GovCon)"
        subtitle="Federal contract exposure from USAspending.gov: agency concentration, recompete risk, and top awards."
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : profile ? (
        <GovConView profile={profile} workspaceId={id} />
      ) : (
        <EmptyState
          title="No federal contract profile yet"
          description="Fetch federal award history from USAspending.gov to profile agency concentration, recompete exposure, and the largest contracts."
          action={
            <div className="w-full max-w-lg text-left">
              <GovConFetchForm
                workspaceId={id}
                defaultName={target?.name}
                label="Fetch federal awards"
              />
            </div>
          }
        />
      )}
    </div>
  );
}
