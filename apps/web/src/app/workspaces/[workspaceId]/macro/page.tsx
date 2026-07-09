import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { Callout } from "@/components/ui/Callout";
import { MacroPanel } from "@/components/MacroPanel";
import type { MacroOverlay } from "@/lib/types";

export default async function MacroPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let macro: MacroOverlay | null = null;
  let error: string | null = null;
  try {
    macro = await api.getMacro(id);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Failed to load the macro overlay.";
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Company"
        title="Macro sensitivity"
        subtitle={
          macro
            ? `FRED indicators relevant to ${macro.target_name}'s sector (${macro.sector}).`
            : "Macro indicators relevant to the target's sector, sourced from FRED."
        }
      />

      {error ? (
        <Callout tone="warning" title="Can't reach the API">
          {error} Start the backend service (<code className="font-mono">apps/api</code>) and refresh.
        </Callout>
      ) : !macro ? (
        <EmptyState
          title="No macro overlay yet"
          description="A macro overlay is built from FRED series relevant to the target's sector once a company is attached to this workspace."
        />
      ) : (
        <>
          <MacroPanel macro={macro} />
          <Callout tone="info">
            Macro series are context for sensitivity analysis, not a forecast. They are informational
            only and are not investment advice.
          </Callout>
        </>
      )}
    </div>
  );
}
