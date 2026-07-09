"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Card } from "@/components/ui/Card";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { titleCase } from "@/lib/formatting";
import type { Priority, RedTeam } from "@/lib/types";

const PRIORITY_TONE: Record<Priority, BadgeTone> = {
  low: "slate",
  medium: "amber",
  high: "red",
};

export function RedTeamViewer({
  redTeam,
  workspaceId,
}: {
  redTeam: RedTeam;
  workspaceId: string;
}) {
  return (
    <div className="space-y-6" data-workspace={workspaceId}>
      <Callout tone="warning" title="Adversarial bear case — for stress-testing only">
        This red-team pack argues against the deal on purpose to surface blind spots. It is a
        challenge document, not a recommendation, and is not investment advice. Trace every counter to
        its{" "}
        <Link
          href={`/workspaces/${workspaceId}/evidence`}
          className="font-medium text-amber-900 underline"
        >
          evidence trail
        </Link>
        .
      </Callout>

      <Card title="Red-team summary">
        <p className="text-sm leading-relaxed text-slate-700">{redTeam.summary}</p>
      </Card>

      <Card title="Bear case" subtitle="The strongest argument against proceeding">
        <div className="memo-prose">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{redTeam.bear_case_markdown}</ReactMarkdown>
        </div>
      </Card>

      <Card
        title="Unsupported claims"
        subtitle={`${redTeam.unsupported_claims.length} claim${
          redTeam.unsupported_claims.length === 1 ? "" : "s"
        } that lean on thin or missing evidence`}
      >
        {redTeam.unsupported_claims.length > 0 ? (
          <Table>
            <THead>
              <TR>
                <TH className="w-1/3">Claim</TH>
                <TH className="w-1/3">Why it&apos;s weak</TH>
                <TH className="w-1/3">Recommended action</TH>
              </TR>
            </THead>
            <TBody>
              {redTeam.unsupported_claims.map((c, i) => (
                <TR key={i} className="hover:bg-slate-50">
                  <TD className="font-medium text-slate-800">{c.claim}</TD>
                  <TD>{c.why_weak}</TD>
                  <TD>{c.recommended_action}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        ) : (
          <p className="text-sm text-slate-500">No unsupported claims were flagged.</p>
        )}
      </Card>

      <Card
        title="Missing evidence"
        subtitle="Gaps that must be closed before conviction is warranted"
      >
        {redTeam.missing_evidence.length > 0 ? (
          <Table>
            <THead>
              <TR>
                <TH className="w-2/5">Item</TH>
                <TH className="w-2/5">Why it matters</TH>
                <TH>Workstream</TH>
              </TR>
            </THead>
            <TBody>
              {redTeam.missing_evidence.map((m, i) => (
                <TR key={i} className="hover:bg-slate-50">
                  <TD className="font-medium text-slate-800">{m.item}</TD>
                  <TD>{m.why_it_matters}</TD>
                  <TD>
                    <Badge tone="indigo">{titleCase(m.workstream)}</Badge>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        ) : (
          <p className="text-sm text-slate-500">No evidence gaps were identified.</p>
        )}
      </Card>

      <Card
        title="High-priority questions"
        subtitle="What to press management on next"
      >
        {redTeam.high_priority_questions.length > 0 ? (
          <ul className="space-y-3">
            {redTeam.high_priority_questions.map((q, i) => (
              <li key={i} className="rounded-lg border border-slate-200 p-4">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-medium text-slate-800">{q.question}</p>
                  <Badge tone={PRIORITY_TONE[q.priority] ?? "slate"}>
                    {titleCase(q.priority)}
                  </Badge>
                </div>
                <p className="mt-1 text-sm text-slate-600">{q.rationale}</p>
                <div className="mt-2">
                  <Badge tone="slate">{q.workstream_label}</Badge>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">No high-priority questions were raised.</p>
        )}
      </Card>
    </div>
  );
}

export default RedTeamViewer;
