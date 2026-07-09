import Link from "next/link";

const CLS =
  "inline-flex items-center rounded-sm bg-accent-soft px-1.5 py-0.5 font-mono text-2xs font-semibold tracking-wide text-accent ring-1 ring-inset ring-[#cfe0ee] transition-colors hover:ring-accent/40";

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
