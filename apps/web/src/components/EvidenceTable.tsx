import { Callout } from "@/components/ui/Callout";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { ClaimBadge } from "@/components/ClaimBadge";
import { formatPct, formatDate } from "@/lib/formatting";
import type { Evidence } from "@/lib/types";

function confidenceColor(confidence: number): string {
  if (confidence >= 0.75) return "bg-green-500";
  if (confidence >= 0.5) return "bg-amber-500";
  return "bg-red-500";
}

function ConfidenceBar({ confidence }: { confidence: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(confidence * 100)));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${confidenceColor(confidence)}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-xs text-slate-500">{formatPct(confidence)}</span>
    </div>
  );
}

export function EvidenceTable({
  evidence,
  workspaceId,
}: {
  evidence: Evidence[];
  workspaceId: string;
}) {
  return (
    <div className="space-y-4" data-workspace={workspaceId}>
      <Callout tone="info" title="Evidence & audit trail">
        Every material claim in this diligence pack is traceable to a row below. Each item is labeled
        by claim type — <span className="font-medium">facts</span>,{" "}
        <span className="font-medium">calculations</span>,{" "}
        <span className="font-medium">inferences</span>, and{" "}
        <span className="font-medium">assumptions</span> — with its source (an XBRL concept or a
        10-K passage on sec.gov) and a confidence read. Outputs are not investment advice.
      </Callout>

      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <Table>
          <THead>
            <TR>
              <TH>Ref</TH>
              <TH>Type</TH>
              <TH className="min-w-[16rem]">Claim</TH>
              <TH className="min-w-[12rem]">Source</TH>
              <TH>Confidence</TH>
              <TH>Agent</TH>
            </TR>
          </THead>
          <TBody>
            {evidence.map((e) => (
              <tr key={e.id} id={e.ref} className="scroll-mt-24 target:bg-brand-50 hover:bg-slate-50">
                <TD>
                  <span className="font-mono text-xs font-medium text-slate-700">{e.ref}</span>
                </TD>
                <TD>
                  <ClaimBadge type={e.claim_type} />
                </TD>
                <TD className="text-slate-800">{e.claim}</TD>
                <TD>
                  {e.source_url ? (
                    <a
                      href={e.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-brand-700 underline decoration-slate-300 underline-offset-2 hover:decoration-brand-500"
                    >
                      {e.source_name}
                    </a>
                  ) : (
                    <span className="font-medium text-slate-800">{e.source_name}</span>
                  )}
                  <div className="mt-0.5 text-xs text-slate-500">
                    {e.source_section ? <span>{e.source_section}</span> : <span>{e.source_type}</span>}
                    {e.source_date && <span> · {formatDate(e.source_date)}</span>}
                  </div>
                </TD>
                <TD>
                  <ConfidenceBar confidence={e.confidence} />
                </TD>
                <TD>
                  <span className="text-xs text-slate-500">{e.agent_name}</span>
                </TD>
              </tr>
            ))}
          </TBody>
        </Table>
      </div>
    </div>
  );
}

export default EvidenceTable;
