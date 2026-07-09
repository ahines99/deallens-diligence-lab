import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Eyebrow } from "@/components/ui/Eyebrow";

const STEPS = [
  {
    n: "01",
    title: "Enter a ticker",
    body: "Open a workspace with a public-company ticker. We resolve it on SEC EDGAR, pull XBRL financials, and ingest recent filings automatically.",
  },
  {
    n: "02",
    title: "Build the plan",
    body: "Generate a workstream diligence plan — commercial, financial, product, legal — with the key questions and evidence each requires.",
  },
  {
    n: "03",
    title: "Screen the red flags",
    body: "Screen against a risk taxonomy — concentration, margin, demand, cyber — extracted from the latest 10-K and scored, ranked, and cited.",
  },
  {
    n: "04",
    title: "Draft the memo",
    body: "Assemble an IC memo and a red-team bear case, every material claim carrying an auditable citation back to its SEC source.",
  },
];

const CAPABILITIES = [
  "Real XBRL financials & multi-year trends",
  "Filing ingestion (10-K / 10-Q / 8-K)",
  "10-K risk-factor extraction",
  "Peer benchmarking by ticker",
  "FRED macro sensitivity overlay",
  "GovCon federal-award diligence",
  "IC memo + red-team bear case",
  "Evidence & audit trail, every claim typed",
];

const TICKERS = ["MSFT", "NVDA", "CRWD", "LDOS", "ORCL", "PLTR"];

export default function HomePage() {
  return (
    <div className="space-y-16">
      {/* Hero */}
      <section className="pt-6">
        <Eyebrow>DealLens Diligence Lab</Eyebrow>
        <h1 className="mt-3 max-w-4xl font-serif text-[2.6rem] font-semibold leading-[1.08] tracking-tight text-ink sm:text-5xl">
          First-pass investment diligence on any public company, grounded in real filings.
        </h1>
        <p className="mt-5 max-w-2xl text-lg leading-relaxed text-muted">
          Enter a ticker. DealLens resolves it on SEC EDGAR, pulls XBRL financials, and reads the latest
          10-K to produce a source-grounded diligence pack — plan, red-flag matrix, peer benchmark,
          diligence questions, an IC memo, and a red-team bear case. Every material claim is cited.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button href="/workspaces/new">Open a workspace</Button>
          <Button href="/workspaces" variant="secondary">
            Browse workspaces
          </Button>
          <div className="ml-1 hidden items-center gap-2 sm:flex">
            {TICKERS.map((t) => (
              <span key={t} className="font-mono text-xs text-faint">
                {t}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section>
        <div className="border-b border-line pb-3">
          <Eyebrow>Method</Eyebrow>
          <h2 className="mt-1 font-serif text-2xl font-semibold text-ink">From ticker to IC memo</h2>
        </div>
        <div className="mt-6 grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s) => (
            <div key={s.n} className="bg-panel p-5">
              <div className="font-serif text-2xl font-semibold text-accent/40">{s.n}</div>
              <h3 className="mt-2 font-sans text-sm font-semibold text-ink">{s.title}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-muted">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Grounded in real data */}
      <section>
        <div className="grid gap-8 rounded-md border border-line bg-panel p-6 shadow-panel lg:grid-cols-[1.35fr_1fr] lg:p-8">
          <div>
            <Eyebrow>Public data, source-grounded</Eyebrow>
            <h2 className="mt-1.5 font-serif text-2xl font-semibold text-ink">
              Real SEC, FRED &amp; USAspending data — no fabrication
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Point DealLens at any public company by ticker. It resolves the CIK on SEC EDGAR, pulls
              standardized XBRL company facts, ingests recent filings, and extracts the 10-K&apos;s risk
              factors — then deterministically drafts the plan, benchmarks against peers you add by
              ticker, overlays FRED macro context, and (for federal contractors) profiles USAspending
              award history. Market valuation multiples are intentionally omitted; qualitative
              severities are heuristic and flagged as such.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button href="/workspaces/new">Start with a ticker</Button>
              <Button href="/workspaces" variant="ghost">
                See it in a workspace →
              </Button>
            </div>
          </div>
          <ul className="grid grid-cols-1 gap-y-2.5 self-center sm:grid-cols-2 lg:grid-cols-1">
            {CAPABILITIES.map((c) => (
              <li key={c} className="flex items-start gap-2.5 text-sm text-body">
                <span className="mt-[7px] h-1 w-3 shrink-0 rounded-full bg-accent/70" aria-hidden />
                {c}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <Callout tone="info" title="Real public data · not investment advice">
        Uses real SEC EDGAR (XBRL + filings), FRED, and USAspending data. Outputs are AI-assisted drafts
        for human review — not investment advice, a recommendation, or an offer of any kind.
      </Callout>
    </div>
  );
}
