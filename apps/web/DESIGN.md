# DealLens UI — "IC-grade" design system

An editorial, consulting-report aesthetic for private-equity diligence. Restraint over decoration:
hairline rules, generous whitespace, tabular figures, a serif display face over clean sans, one
confident blue accent + restrained gold. **No** heavy drop-shadows, no big rounded bubbles, no
SaaS-purple, no gradients.

## Type
- Display (page titles, card-worthy headings, memo): **serif** → `font-serif` (Newsreader).
- Body / UI / labels: **sans** → default `font-sans` (Inter).
- Numbers: always `tabular-nums` (already default in tables and StatTile).
- Kicker/eyebrow: `.eyebrow` class or `<Eyebrow>` — 11px, uppercase, tracked, accent color.

## Color tokens (Tailwind, semantic)
- Text: `text-ink` (headings/navy), `text-body` (default), `text-muted`, `text-faint`.
- Surfaces: page is `bg-paper`; panels `bg-panel` (white); subtle fills `bg-panel2` / `bg-sunken`.
- Hairlines: `border-line` (default), `border-line-faint`, `border-line-strong`.
- Accent: `bg-accent` / `text-accent` / `bg-accent-soft` (deep pro blue #0B4F82). `text-gold` / `bg-gold-soft`.
- Status/severity: `text-severity-{low|medium|high|critical}`, `text-positive`, `text-negative`.
- DO NOT use raw `slate-*` / `indigo-*` / `brand-*` in restyled files — use the tokens above.

## Primitives (import from `@/components/ui`)
- `<PageHeader eyebrow title subtitle actions />` — EVERY page starts with this (serif title + hairline under).
- `<Card title? subtitle? eyebrow? right?>` — white panel, hairline border, `shadow-panel`. Body padded.
- `<StatTile label value sub? tone? />` — KPI: uppercase micro label, big tabular value. `tone`: default|positive|negative|accent.
- `<Badge tone>` — small uppercase pill. tones: neutral|green|amber|red|critical|indigo|slate|gold.
- `<Callout tone title>` — thin left-border note. tones: info|warning|synthetic|muted.
- `<Button variant>` — primary|secondary|ghost.
- `<DataTable columns rows getRowKey? empty? rowId? />` or `<Table/THead/TBody/TR/TH/TD>` — hairline table,
  uppercase micro header with a strong bottom rule, numerics right-aligned + tabular.
- `<Eyebrow>` — standalone kicker label.

## Layout patterns
- Page = `<PageHeader …/>` then `space-y-6` sections, each usually a `<Card>`.
- KPI row: `grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-px bg-line` with each StatTile in a `bg-panel p-4`
  cell → produces a hairline-divided metric strip (the consulting look). (Or a plain gap grid.)
- Tables: right-align every numeric column (`align="right"`), keep labels left. Use Badges for status/severity.
- Severity → Badge tone: low→green, medium→amber, high→red, critical→critical.
- Empty/not-generated states use `<EmptyState>`; API-down uses `<Callout tone="warning">`.
- Keep all disclaimers ("not investment advice", data-source notes) as `<Callout>`.

## Charts (import from `@/lib/chartTheme`)
- `CHART_SERIES` (validated fixed order), `CHART.grid` / `CHART.axis` / `CHART.axisTick` / `CHART.accent`,
  `SERIES_COLOR` (metric→hex), `tickStyle`.
- Recessive grid (`CHART.grid`, no vertical lines), thin axes, 11px ticks in `CHART.axisTick`, legend for ≥2 series.
- **NEVER dual-axis.** The old TrendChart mixed revenue bars + margin lines on two y-axes — split it into
  TWO charts: a revenue bar chart (USD) and a margins line chart (percent, series = gross/operating/net via SERIES_COLOR).
- Bars: thin, `radius={[2,2,0,0]}`, series color; single-series (revenue, agency amounts) → `CHART.accent`.
- Tooltips on, styled minimally (white, hairline border). Do not put values on every point.

## Do / Don't
- DO: hairlines, whitespace, uppercase micro-labels, serif titles, tabular numbers, restrained accent.
- DON'T: bright indigo, chunky rounded cards, drop shadows beyond `shadow-panel`/`shadow-sm`, emoji as UI chrome,
  changing any data/logic. This is a visual pass only.
