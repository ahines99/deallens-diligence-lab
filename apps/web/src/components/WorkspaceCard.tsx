import Link from "next/link";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { DEAL_TYPE_LABELS, formatDate } from "@/lib/formatting";
import type { Workspace, WorkspaceStatus } from "@/lib/types";

const STATUS_TONE: Record<WorkspaceStatus, BadgeTone> = {
  draft: "slate",
  in_progress: "amber",
  complete: "green",
};

const STATUS_LABEL: Record<WorkspaceStatus, string> = {
  draft: "Draft",
  in_progress: "In progress",
  complete: "Complete",
};

export function WorkspaceCard({ workspace }: { workspace: Workspace }) {
  return (
    <Link
      href={`/workspaces/${workspace.id}`}
      className="group flex h-full flex-col rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition hover:border-brand-300 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-semibold text-slate-900 group-hover:text-brand-700">{workspace.name}</h3>
        <Badge tone={STATUS_TONE[workspace.status]}>{STATUS_LABEL[workspace.status]}</Badge>
      </div>
      <div className="mt-2">
        <Badge tone="indigo">{DEAL_TYPE_LABELS[workspace.deal_type] ?? workspace.deal_type}</Badge>
      </div>
      <p className="mt-3 line-clamp-3 text-sm text-slate-600">{workspace.investment_question}</p>
      <div className="mt-auto pt-4 text-xs text-slate-400">
        Created {formatDate(workspace.created_at)}
      </div>
    </Link>
  );
}

export default WorkspaceCard;
