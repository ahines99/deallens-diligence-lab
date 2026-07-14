import { Badge } from "@/components/ui/Badge";
import { SourceStatusCallout } from "@/components/SourceStatusCallout";
import { formatDate } from "@/lib/formatting";
import type { EventTimeline as EventTimelineData } from "@/lib/types";

export function EventTimeline({ data }: { data: EventTimelineData }) {
  if (data.source_status === "unavailable") {
    return <SourceStatusCallout status={data.source_status} error={data.source_error} source="SEC filing events" />;
  }
  if (data.events.length === 0) {
    return <div className="space-y-4"><SourceStatusCallout status={data.source_status} error={data.source_error} source="SEC filing events" /><p className="py-6 text-center text-sm text-muted">{data.source_status === "partial" ? "No events were returned from the partial response; coverage is incomplete." : "No filing events found."}</p></div>;
  }
  return (
    <div className="space-y-5">
      <SourceStatusCallout status={data.source_status} error={data.source_error} source="SEC filing events" />
    <ol className="relative space-y-6 border-l border-line pl-6">
      {data.events.map((ev, i) => (
        <li key={`${ev.accession ?? i}-${ev.date}`} className="relative">
          <span
            className={`absolute -left-[1.72rem] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-panel ${
              ev.significant ? "bg-severity-high" : "bg-accent"
            }`}
            aria-hidden
          />
          <div className="flex flex-wrap items-center gap-2">
            <span className="tabular-nums text-xs font-semibold text-ink">{formatDate(ev.date)}</span>
            <Badge tone="slate">{ev.form}</Badge>
            {ev.significant && <Badge tone="red">Significant</Badge>}
            {ev.url && (
              <a
                href={ev.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-2xs font-semibold uppercase tracking-eyebrow text-accent hover:underline"
              >
                Filing ↗
              </a>
            )}
          </div>
          {ev.items.length > 0 ? (
            <ul className="mt-1.5 space-y-0.5">
              {ev.items.map((it) => (
                <li key={it.code} className="text-xs leading-snug text-muted">
                  <span className="font-mono text-2xs text-faint">{it.code}</span> — {it.label}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-xs text-faint">No itemized events.</p>
          )}
        </li>
      ))}
    </ol>
    </div>
  );
}

export default EventTimeline;
