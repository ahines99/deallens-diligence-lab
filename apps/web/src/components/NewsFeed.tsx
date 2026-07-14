import { Callout } from "@/components/ui/Callout";
import { SourceStatusCallout } from "@/components/SourceStatusCallout";
import { formatDate } from "@/lib/formatting";
import type { NewsSignals } from "@/lib/types";

// GDELT seendate is like "20250704T120000Z"; render just the date part.
function gdeltDate(s: string): string {
  if (!s) return "—";
  if (/^\d{8}T/.test(s)) {
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  }
  return formatDate(s);
}

export function NewsFeed({ data }: { data: NewsSignals }) {
  return (
    <div className="space-y-5">
      <Callout tone="warning" title="Unverified media — not evidence">
        These articles come from GDELT&apos;s open news index and are shown as market-signal context
        only. They are <strong>not</strong> part of the evidence table and must be independently
        verified before any use in diligence or the IC memo.
      </Callout>

      <SourceStatusCallout status={data.source_status} error={data.source_error} source="GDELT news" />

      {data.source_status === "unavailable" ? null : data.articles.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">
          {data.source_status === "partial"
            ? "No articles were returned from the partial response; coverage is incomplete."
            : "No recent articles found."}
        </p>
      ) : (
        <ul className="divide-y divide-line-faint">
          {data.articles.map((a, i) => (
            <li key={`${a.url}-${i}`} className="flex flex-col gap-1 py-3">
              <a
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm font-medium leading-snug text-ink hover:text-accent hover:underline"
              >
                {a.title || a.url}
              </a>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-muted">
                <span className="font-medium text-body">{a.domain}</span>
                <span aria-hidden>·</span>
                <span className="tabular-nums">{gdeltDate(a.seendate)}</span>
                {a.sourcecountry && (
                  <>
                    <span aria-hidden>·</span>
                    <span>{a.sourcecountry}</span>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default NewsFeed;
