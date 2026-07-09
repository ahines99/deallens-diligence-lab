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
      className="group flex h-full flex-col rounded-md border border-line bg-panel p-5 shadow-panel transition-colors hover:border-accent/40"
    >
      <div className="flex items-center justify-between gap-3">
        <Badge tone="indigo">{DEAL_TYPE_LABELS[workspace.deal_type] ?? workspace.deal_type}</Badge>
        <Badge tone={STATUS_TONE[workspace.status]}>{STATUS_LABEL[workspace.status]}</Badge>
      </div>
      <h3 className="mt-3 font-serif text-lg font-semibold leading-snug text-ink transition-colors group-hover:text-accent">
        {workspace.name}
      </h3>
      <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted">
        {workspace.investment_question}
      </p>
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-line-faint pt-3">
        <span className="text-2xs uppercase tracking-eyebrow text-faint">Created</span>
        <span className="font-mono text-2xs tabular-nums text-faint">
          {formatDate(workspace.created_at)}
        </span>
      </div>
    </Link>
  );
}

export default WorkspaceCard;
