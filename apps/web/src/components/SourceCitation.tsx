import Link from "next/link";

const CLS =
  "inline-flex items-center rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[11px] font-medium text-slate-600 transition-colors hover:border-brand-300 hover:text-brand-700";

export function SourceCitation({
  evidenceRef,
  workspaceId,
}: {
  evidenceRef: string;
  workspaceId?: string;
}) {
  if (workspaceId) {
    return (
      <Link
        href={`/workspaces/${workspaceId}/evidence#${evidenceRef}`}
        className={CLS}
        title={`View evidence ${evidenceRef}`}
      >
        {evidenceRef}
      </Link>
    );
  }
  return (
    <span className={CLS} title={`Evidence ${evidenceRef}`}>
      {evidenceRef}
    </span>
  );
}

export default SourceCitation;
