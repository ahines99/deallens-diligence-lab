import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/Table";
import { formatDate } from "@/lib/formatting";
import type { ThemeScan } from "@/lib/types";

export function ThemeScanPanel({ data }: { data: ThemeScan }) {
  const total = data.themes.reduce((n, t) => n + t.count, 0);
  return (
    <Card
      title="Red-flag theme scan"
      subtitle="Full-text search of the target's SEC filings for high-risk language"
      right={<Badge tone={total > 0 ? "amber" : "green"}>{total} hits</Badge>}
    >
      <Table>
        <THead>
          <TR>
            <TH>Theme</TH>
            <TH align="right">Hits</TH>
            <TH>Recent filings</TH>
          </TR>
        </THead>
        <TBody>
          {data.themes.map((t) => (
            <TR key={t.theme} className="hover:bg-panel2">
              <TD className="font-medium text-ink">{t.label}</TD>
              <TD align="right">
                {t.count > 0 ? (
                  <Badge tone="red">{t.count}</Badge>
                ) : (
                  <span className="text-faint">0</span>
                )}
              </TD>
              <TD>
                {t.hits.length === 0 ? (
                  <span className="text-2xs text-faint">No matches</span>
                ) : (
                  <div className="flex flex-wrap gap-x-3 gap-y-1">
                    {t.hits.map((h, i) => {
                      const label = `${h.form} · ${formatDate(h.date)}`;
                      return h.url ? (
                        <a
                          key={i}
                          href={h.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-2xs font-medium text-accent hover:underline"
                        >
                          {label} ↗
                        </a>
                      ) : (
                        <span key={i} className="text-2xs text-muted">
                          {label}
                        </span>
                      );
                    })}
                  </div>
                )}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

export default ThemeScanPanel;
