import { notFound } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatTile, type StatTone } from "@/components/ui/StatTile";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { GenerateButton } from "@/components/GenerateButton";
import { RiskMatrix } from "@/components/RiskMatrix";
import type { RiskFinding, Severity } from "@/lib/types";

const SEVERITY_TILE_TONE: Record<Severity, StatTone> = {
  critical: "red",
  high: "red",
  medium: "amber",
  low: "green",
};

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

export default async function RisksPage({
  params,
}: {
  params: { workspaceId: string };
}) {
  const id = params.workspaceId;

  let risks: RiskFinding[];
  try {
    risks = await api.getRisks(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    return (
      <Callout tone="warning" title="Can't reach the API">
        {e instanceof ApiError ? e.message : "Failed to load red flags."} Start the backend
        service (<code className="font-mono">apps/api</code>) and refresh.
      </Callout>
    );
  }

  if (risks.length === 0) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Red-flag matrix"
          subtitle="AI-screened risks across the deal, ranked by severity and tied to evidence."
        />
        <EmptyState
          title="No red flags generated yet"
          description="Run the risk screen to surface the target's highest-severity red flags from its real 10-K risk factors and XBRL financials — each tied to evidence for human review."
          action={<GenerateButton kind="risks" workspaceId={id} label="Screen for red flags" />}
        />
      </div>
    );
  }

  const counts = SEVERITIES.map((sev) => ({
    sev,
    count: risks.filter((r) => r.severity === sev).length,
  }));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Red-flag matrix"
        subtitle={`${risks.length} finding${risks.length === 1 ? "" : "s"}, ranked by severity and tied to evidence.`}
        actions={
          <GenerateButton
            kind="risks"
            workspaceId={id}
            label="Regenerate"
            variant="secondary"
          />
        }
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {counts.map(({ sev, count }) => (
          <StatTile
            key={sev}
            label={SEVERITY_LABEL[sev]}
            value={count}
            tone={count > 0 ? SEVERITY_TILE_TONE[sev] : "neutral"}
          />
        ))}
      </div>

      <Callout tone="info" title="AI-drafted from real SEC filings">
        These findings were drafted from the company's real 10-K risk factors and XBRL financials.
        Qualitative severities are heuristic; treat as decision-support for human review — not investment advice.
      </Callout>

      <RiskMatrix risks={risks} workspaceId={id} />
    </div>
  );
}
