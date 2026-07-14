import type { ReactNode } from "react";
import Link from "next/link";
import { api } from "@/lib/serverApi";
import { WorkspaceNav } from "@/components/WorkspaceNav";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import type { WorkspaceOverview } from "@/lib/types";
import { WorkspaceGovernanceControl } from "@/components/governance/WorkspaceGovernanceControl";

const STATUS_TONE: Record<string, BadgeTone> = {
  draft: "slate",
  in_progress: "amber",
  complete: "green",
};

export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId: id } = await params;
  const base = `/workspaces/${id}`;

  let ov: WorkspaceOverview | null = null;
  try {
    ov = await api.getWorkspace(id);
  } catch {
    ov = null;
  }
  const ws = ov?.workspace;
  const target = ov?.target;

  return (
    <div className="grid gap-8 lg:grid-cols-[248px_minmax(0,1fr)]">
      <aside className="lg:sticky lg:top-[4.5rem] lg:self-start">
        <Link
          href="/workspaces"
          className="mb-4 inline-flex items-center gap-1 text-2xs font-semibold uppercase tracking-eyebrow text-muted transition hover:text-accent"
        >
          ← All workspaces
        </Link>

        <div className="mb-5 rounded-md border border-line bg-panel p-3.5 shadow-panel">
          <div className="flex flex-wrap items-center gap-1.5">
            {target?.ticker && <Badge tone="indigo">{target.ticker}</Badge>}
            {ws && (
              <Badge tone={STATUS_TONE[ws.status] ?? "slate"}>{ws.status.replace("_", " ")}</Badge>
            )}
          </div>
          <div className="mt-2 font-serif text-[0.95rem] font-semibold leading-tight text-ink line-clamp-2">
            {target?.name ?? ws?.name ?? "Workspace"}
          </div>
          {target?.sector && (
            <div className="mt-1 text-2xs leading-snug text-muted line-clamp-2">{target.sector}</div>
          )}
        </div>

        <WorkspaceGovernanceControl workspaceId={id} initialWorkspace={ws} />

        <WorkspaceNav base={base} />
      </aside>

      <div className="min-w-0 space-y-6">{children}</div>
    </div>
  );
}
