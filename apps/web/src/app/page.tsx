import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Callout } from "@/components/ui/Callout";
import { Badge } from "@/components/ui/Badge";

const STEPS = [
  {
    n: 1,
    title: "Enter a ticker",
    body: "Open a workspace with a public-company ticker. We resolve it on SEC EDGAR, pull its XBRL financials, and ingest its recent filings automatically.",
  },
  {
    n: 2,
    title: "Build the plan",
    body: "Generate a workstream diligence plan — commercial, financial, product, legal — with the key questions and evidence each needs.",
  },
  {
    n: 3,
    title: "Screen the red flags",
    body: "Screen the company against a risk taxonomy — concentration, margin, demand, cyber — extracted from its latest 10-K and scored, ranked, and tied to evidence.",
  },
  {
    n: 4,
    title: "Draft the memo",
    body: "Assemble an IC memo and a red-team bear case, with every material claim carrying an auditable citation back to its SEC source.",
  },
];

const CAPABILITIES = [
  "Real XBRL financials from SEC company facts",
  "Recent filing ingestion (10-K, 10-Q, 8-K)",
  "10-K risk-factor extraction",
  "Peer benchmarking by ticker",
  "Red-flag risk matrix",
  "Prioritized diligence questions",
  "IC memo + bear-case generation",
  "Evidence & audit trail with claim typing",
];

const TICKERS = ["MSFT", "NVDA", "CRWD", "ORCL", "CRM", "SPSC"];

export default function HomePage() {
  return (
    <div className="space-y-14">
      {/* Hero */}
      <section className="pt-4">
        <Badge tone="indigo">Real SEC-data AI diligence copilot</Badge>
        <h1 className="mt-4 max-w-3xl text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl">
          DealLens Diligence Lab
        </h1>
        <p className="mt-4 max-w-2xl text-lg text-slate-600">
          Enter a public-company ticker and DealLens builds a source-grounded diligence pack from that
          company&apos;s real SEC filings and XBRL financials — plan, red flags, questions, IC memo, and
          a red-team bear case, every claim cited.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Button href="/workspaces/new">Try a ticker (MSFT, NVDA, CRWD…)</Button>
          <Button href="/workspaces" variant="secondary">
            Browse workspaces
          </Button>
        </div>
      </section>

      {/* What it does */}
      <section>
        <h2 className="text-xl font-semibold text-slate-900">How it works</h2>
        <p className="mt-1 text-sm text-slate-500">
          From a ticker to an evidence-backed IC memo in four steps.
        </p>
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s) => (
            <div key={s.n} className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">
                {s.n}
              </div>
              <h3 className="mt-3 font-semibold text-slate-900">{s.title}</h3>
              <p className="mt-1 text-sm text-slate-600">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Real-data pitch */}
      <section>
        <Card className="overflow-hidden">
          <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr] lg:items-center">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-xl font-semibold text-slate-900">Grounded in real SEC data</h2>
                <Badge tone="green">Live EDGAR</Badge>
              </div>
              <p className="mt-3 text-sm leading-relaxed text-slate-600">
                Point DealLens at any public company by its ticker. It resolves the CIK on SEC EDGAR,
                pulls standardized XBRL company facts for the financials, ingests recent filings, and
                extracts the latest 10-K&apos;s risk factors. From there it deterministically drafts a
                diligence plan, benchmarks the company against peers you add by ticker, surfaces red
                flags, writes diligence questions, and produces an IC memo with a red-team bear case —
                every material claim cited back to its source. Market valuation multiples are
                intentionally omitted (no free source).
              </p>
              <div className="mt-5 flex flex-wrap gap-2">
                {TICKERS.map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-slate-300 bg-white px-3 py-1 font-mono text-xs font-medium text-slate-600"
                  >
                    {t}
                  </span>
                ))}
              </div>
              <div className="mt-5 flex flex-wrap gap-3">
                <Button href="/workspaces/new">Start with a ticker</Button>
                <Button href="/workspaces" variant="ghost">
                  See it in a workspace
                </Button>
              </div>
            </div>
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-1">
              {CAPABILITIES.map((c) => (
                <li key={c} className="flex items-start gap-2 text-sm text-slate-700">
                  <span className="mt-1 text-brand-600" aria-hidden>
                    ✓
                  </span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        </Card>
      </section>

      {/* Disclaimer */}
      <section>
        <Callout tone="info" title="Real SEC EDGAR data · not investment advice">
          Uses real SEC EDGAR data (XBRL company facts and public filings). Outputs are AI-assisted
          drafts for human review — not investment advice, a recommendation, or an offer of any kind.
        </Callout>
      </section>
    </div>
  );
}
