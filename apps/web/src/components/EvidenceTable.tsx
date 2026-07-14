"use client";

import { Fragment, useState } from "react";
import { Card } from "@/components/ui/Card";
import { Callout } from "@/components/ui/Callout";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { ClaimBadge } from "@/components/ClaimBadge";
import { formatPct, formatDate } from "@/lib/formatting";
import type { Evidence } from "@/lib/types";

function ConfidenceBar({ confidence }: { confidence: number }) {
  const value = Math.max(0, Math.min(100, Math.round(confidence * 100)));
  const fill = confidence >= 0.75 ? "bg-positive" : confidence >= 0.5 ? "bg-severity-medium" : "bg-negative";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-sunken">
        <div className={`h-full rounded-full ${fill}`} style={{ width: `${value}%` }} />
      </div>
      <span className="tabular-nums text-xs text-muted">{formatPct(confidence)}</span>
    </div>
  );
}

export function EvidenceTable({ evidence, workspaceId }: { evidence: Evidence[]; workspaceId: string }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-4" data-workspace={workspaceId}>
      <Callout tone="info" title="Evidence & audit trail">
        Every material claim is traceable to a source. Expand a row to inspect the retained source
        excerpt rather than relying on the summarized claim alone.
      </Callout>
      <Card>
        <Table>
          <THead>
            <TR className="border-0"><TH>Ref</TH><TH>Type</TH><TH className="min-w-[16rem]">Claim</TH><TH className="min-w-[12rem]">Source</TH><TH>Confidence</TH><TH>Review</TH></TR>
          </THead>
          <TBody>
            {evidence.map((item) => {
              const open = expanded.has(item.id);
              const panelId = `evidence-text-${item.id}`;
              return (
                <Fragment key={item.id}>
                  <tr id={item.ref} className="scroll-mt-24 border-b border-line-faint target:bg-accent-soft hover:bg-panel2">
                    <TD><span className="font-mono text-xs font-medium text-body">{item.ref}</span></TD>
                    <TD><ClaimBadge type={item.claim_type} /></TD>
                    <TD className="text-body">{item.claim}</TD>
                    <TD>
                      {item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer" className="font-medium text-accent underline decoration-line-strong underline-offset-2 hover:decoration-accent">{item.source_name}</a> : <span className="font-medium text-ink">{item.source_name}</span>}
                      <div className="mt-0.5 text-xs text-muted">{item.source_section || item.source_type}{item.source_date && <span> · {formatDate(item.source_date)}</span>}</div>
                    </TD>
                    <TD><ConfidenceBar confidence={item.confidence} /></TD>
                    <TD>
                      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => toggle(item.id)} className="rounded px-2 py-1 text-xs font-semibold text-accent hover:bg-accent-soft">
                        {open ? "Hide excerpt" : "View excerpt"}
                      </button>
                      <div className="mt-1 text-2xs text-faint">{item.agent_name}</div>
                    </TD>
                  </tr>
                  {open && (
                    <tr id={panelId} className="border-b border-line bg-panel2">
                      <td colSpan={6} className="px-4 py-4">
                        <div className="eyebrow mb-2">Retained source excerpt</div>
                        {item.evidence_text ? (
                          <blockquote className="max-w-5xl whitespace-pre-wrap border-l-2 border-accent bg-panel px-4 py-3 text-sm leading-relaxed text-body">{item.evidence_text}</blockquote>
                        ) : (
                          <p className="text-xs text-muted">No source excerpt was retained for this evidence row. Use the source link and section metadata for verification.</p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </TBody>
        </Table>
      </Card>
    </div>
  );
}

export default EvidenceTable;
