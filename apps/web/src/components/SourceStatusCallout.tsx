import { Callout } from "@/components/ui/Callout";
import type { ExternalSourceStatus } from "@/lib/types";

export function SourceStatusCallout({
  status,
  error,
  source,
}: {
  status: ExternalSourceStatus;
  error: string | null;
  source: string;
}) {
  if (status === "available") return null;
  const unavailable = status === "unavailable";
  return (
    <Callout
      tone={unavailable ? "warning" : "info"}
      title={`${source} ${unavailable ? "unavailable" : "partially available"}`}
    >
      {error || (unavailable
        ? "The upstream source could not be reached. No conclusion should be drawn from missing results."
        : "Some upstream requests failed. Displayed results may be incomplete.")}
    </Callout>
  );
}
