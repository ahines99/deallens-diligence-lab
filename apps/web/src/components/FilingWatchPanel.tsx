"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { formatDate } from "@/lib/formatting";
import type { FilingWatch } from "@/lib/types";

export function FilingWatchPanel({
  workspaceId,
  initial,
}: {
  workspaceId: string;
  initial: FilingWatch | null;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function onRefresh() {
    if (loading) return;
    setLoading(true);
    setError(null);
    setDone(null);
    try {
      await api.refreshWorkspace(workspaceId);
      setDone("Re-ingested the latest filings from SEC EDGAR and re-ran analysis.");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 502 ? "Couldn't reach SEC EDGAR. Try again in a moment." : err.message);
      } else {
        setError("Failed to refresh from SEC.");
      }
    } finally {
      setLoading(false);
    }
  }

  const hasNew = initial?.has_new ?? false;

  return (
    <Card
      title="Filing watch"
      subtitle={
        initial?.last_ingested_date
          ? `Last ingested filing: ${formatDate(initial.last_ingested_date)}`
          : "Compares SEC EDGAR against filings stored in this workspace"
      }
      right={
        <Button variant="secondary" onClick={onRefresh} disabled={loading}>
          {loading ? (
            <>
              <span
                className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
                aria-hidden
              />
              Refreshing…
            </>
          ) : (
            "Refresh from SEC"
          )}
        </Button>
      }
    >
      <div className="space-y-4">
        {initial === null ? (
          <p className="text-sm text-muted">
            Filing-watch status is unavailable. Ingest a public company with a CIK, then refresh.
          </p>
        ) : hasNew ? (
          <>
            <div className="flex items-center gap-2">
              <Badge tone="amber">{initial.new_filings.length} new</Badge>
              <span className="text-sm text-body">New filings are available on SEC EDGAR.</span>
            </div>
            <Table>
              <THead>
                <TR>
                  <TH>Form</TH>
                  <TH>Date</TH>
                  <TH>Accession</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {initial.new_filings.map((f, i) => (
                  <TR key={`${f.accession ?? i}`}>
                    <TD>
                      <Badge tone="slate">{f.form}</Badge>
                    </TD>
                    <TD className="tabular-nums">{formatDate(f.date)}</TD>
                    <TD className="font-mono text-2xs text-muted">{f.accession ?? "—"}</TD>
                    <TD>
                      {f.url && (
                        <a
                          href={f.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-2xs font-semibold uppercase tracking-eyebrow text-accent hover:underline"
                        >
                          View ↗
                        </a>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </>
        ) : (
          <div className="flex items-center gap-2">
            <Badge tone="green">Up to date</Badge>
            <span className="text-sm text-muted">No new filings since the last ingestion.</span>
          </div>
        )}

        {done && (
          <Callout tone="info" title="Refreshed">
            {done}
          </Callout>
        )}
        {error && (
          <Callout tone="warning" title="Couldn't refresh">
            {error}
          </Callout>
        )}
      </div>
    </Card>
  );
}

export default FilingWatchPanel;
