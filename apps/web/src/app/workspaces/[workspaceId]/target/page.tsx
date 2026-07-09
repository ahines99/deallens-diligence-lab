import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { Button } from "@/components/ui/Button";
import { TargetProfile } from "@/components/TargetProfile";
import type { Target } from "@/lib/types";

export default async function TargetPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let target: Target | null = null;
  try {
    target = await api.getTarget(id);
  } catch (e) {
    return (
      <div className="space-y-6">
        <PageHeader title="Target" />
        <Callout tone="warning" title="Can't reach the API">
          {e instanceof ApiError ? e.message : "Failed to load the target."} Start the backend
          service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Target"
        subtitle="The company under diligence — profile and headline financials that anchor benchmarks and risk screening."
      />

      {target ? (
        <TargetProfile target={target} />
      ) : (
        <EmptyState
          title="No target attached"
          description="This workspace has no target company yet. Create a workspace with a public-company ticker to attach a real target with SEC financials and filings."
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
