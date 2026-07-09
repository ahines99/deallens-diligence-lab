import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/ui/StatTile";
import { Callout } from "@/components/ui/Callout";
import { DataTable, type Column } from "@/components/ui/Table";
import { AgencyConcentrationChart } from "@/components/AgencyConcentrationChart";
import { GovConFetchForm } from "@/components/GovConFetchForm";
import { formatDate, formatNumber, formatPct, formatUsd } from "@/lib/formatting";
import type { AgencyShare, GovConAward, GovConProfile, RecompeteAward } from "@/lib/types";

function truncate(text: string, max = 120): string {
  if (!text) return "—";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const AGENCY_COLUMNS: Column<AgencyShare>[] = [
  {
    key: "agency",
    header: "Agency",
    render: (r) => <span className="font-medium text-ink">{r.agency ?? "Unknown"}</span>,
  },
  {
    key: "amount",
    header: "Obligations",
    align: "right",
    render: (r) => <span className="tabular-nums text-body">{formatUsd(r.amount)}</span>,
  },
  {
    key: "pct",
    header: "Share",
    align: "right",
    render: (r) => <span className="tabular-nums text-body">{formatPct(r.pct)}</span>,
  },
];

const RECOMPETE_COLUMNS: Column<RecompeteAward>[] = [
  {
    key: "agency",
    header: "Agency",
    render: (r) => <span className="text-body">{r.agency ?? "—"}</span>,
  },
  {
    key: "amount",
    header: "Amount",
    align: "right",
    render: (r) => <span className="tabular-nums text-body">{formatUsd(r.amount)}</span>,
  },
  {
    key: "pop_end",
    header: "PoP end",
    align: "right",
    render: (r) => <span className="tabular-nums text-muted">{formatDate(r.pop_end)}</span>,
  },
];

const AWARD_COLUMNS: Column<GovConAward>[] = [
  {
    key: "award_id",
    header: "Award ID",
    render: (r) => (
      <span className="font-mono text-xs text-body">{r.award_id ?? "—"}</span>
    ),
  },
  {
    key: "agency",
    header: "Agency",
    render: (r) => <span className="text-body">{r.agency ?? "—"}</span>,
  },
  {
    key: "amount",
    header: "Amount",
    align: "right",
    render: (r) => <span className="tabular-nums text-body">{formatUsd(r.amount)}</span>,
  },
  {
    key: "pop_end",
    header: "PoP end",
    align: "right",
    render: (r) => <span className="tabular-nums text-muted">{formatDate(r.pop_end)}</span>,
  },
  {
    key: "description",
    header: "Description",
    render: (r) => (
      <span className="block max-w-md text-xs text-muted">{truncate(r.description)}</span>
    ),
  },
];

export function GovConView({
  profile,
  workspaceId,
}: {
  profile: GovConProfile;
  workspaceId: string;
}) {
  return (
    <div className="space-y-6">
      <Card
        title="Federal contract profile"
        subtitle={profile.recipient_name}
        right={<span className="text-xs text-faint">Updated {formatDate(profile.created_at)}</span>}
      >
        <GovConFetchForm
          workspaceId={workspaceId}
          defaultName={profile.recipient_name}
          label="Refresh"
        />
      </Card>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatTile
          label="Total obligations"
          value={formatUsd(profile.total_obligations)}
          tone="accent"
        />
        <StatTile label="Award count" value={formatNumber(profile.award_count)} />
        <StatTile
          label="Top agency"
          value={profile.top_agency ?? "—"}
          sub={
            profile.top_agency_pct !== null && profile.top_agency_pct !== undefined
              ? `${formatPct(profile.top_agency_pct)} of obligations`
              : undefined
          }
        />
      </div>

      <Callout tone="info">
        Federal award data from USAspending.gov (contracts). Recompete reflects awards with a
        period-of-performance end date in the data; some large parent awards omit it.
      </Callout>

      <Card title="Agency concentration" subtitle="Obligations by awarding agency">
        <div className="space-y-6">
          <AgencyConcentrationChart rows={profile.agency_concentration} />
          <DataTable
            columns={AGENCY_COLUMNS}
            rows={profile.agency_concentration}
            getRowKey={(r, i) => `${r.agency ?? "unknown"}-${i}`}
            empty="No agency concentration data."
          />
        </div>
      </Card>

      <Card
        title="Recompete exposure"
        subtitle={`${formatNumber(profile.recompete.count)} awards · ${formatUsd(
          profile.recompete.value,
        )} at risk`}
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:max-w-md">
            <StatTile
              label="Recompete awards"
              value={formatNumber(profile.recompete.count)}
              tone="amber"
            />
            <StatTile
              label="Value at recompete"
              value={formatUsd(profile.recompete.value)}
              tone="amber"
            />
          </div>
          <DataTable
            columns={RECOMPETE_COLUMNS}
            rows={profile.recompete.awards}
            getRowKey={(r, i) => `${r.award_id ?? "award"}-${i}`}
            empty="No awards with a period-of-performance end date."
          />
        </div>
      </Card>

      <Card title="Top awards" subtitle="Largest federal contract awards by obligation">
        <DataTable
          columns={AWARD_COLUMNS}
          rows={profile.top_awards}
          getRowKey={(r, i) => `${r.award_id ?? "award"}-${i}`}
          empty="No awards found."
        />
      </Card>
    </div>
  );
}

export default GovConView;
