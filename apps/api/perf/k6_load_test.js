// G37 — Load-test harness for the DealLens hot endpoints.
//
// k6 is the heavier, OUT-OF-CI load test: it drives a *running* API instance over HTTP with
// concurrent virtual users and staged ramp-up, and fails the run if the measured p95 latencies
// breach the budgets in perf/perf_budget.json. It is NOT a Python/pip dependency — install the
// standalone `k6` binary (https://k6.io/docs/get-started/installation/) and run it against a live
// server. The always-on CI gate is instead the in-process pytest smoke (tests/test_perf_smoke.py),
// which needs no external tooling. See perf/README.md.
//
// Usage:
//   1. Start the API (mock LLM, no network):
//        LLM_MODE=mock AUTO_SEED=false uvicorn src.main:app --port 8000
//   2. Create a workspace with fixture filings/chunks (see perf/README.md for a one-liner), then:
//        k6 run -e BASE_URL=http://localhost:8000 -e WORKSPACE_ID=<id> perf/k6_load_test.js
//
// Thresholds below mirror perf/perf_budget.json. Keep them in sync when budgets change.

import http from 'k6/http';
import { check, group } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const WORKSPACE_ID = __ENV.WORKSPACE_ID || '';
const TOKEN = __ENV.TOKEN || ''; // optional dls_/dlk_ bearer when AUTH_REQUIRED=true

// p95 budgets (ms) — must match perf/perf_budget.json.
const BUDGET = {
  list: 150,
  overview: 250,
  qa: 500,
  search: 500,
  underwriting: 500,
};

export const options = {
  scenarios: {
    hot_endpoints: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '15s', target: 10 }, // ramp up
        { duration: '30s', target: 10 }, // sustained load
        { duration: '10s', target: 20 }, // burst
        { duration: '5s', target: 0 },   // ramp down
      ],
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    'http_req_failed': ['rate<0.01'],
    'http_req_duration{endpoint:list}': [`p(95)<${BUDGET.list}`],
    'http_req_duration{endpoint:overview}': [`p(95)<${BUDGET.overview}`],
    'http_req_duration{endpoint:qa}': [`p(95)<${BUDGET.qa}`],
    'http_req_duration{endpoint:search}': [`p(95)<${BUDGET.search}`],
    'http_req_duration{endpoint:underwriting}': [`p(95)<${BUDGET.underwriting}`],
  },
};

const ASSUMPTIONS = {
  historical: {
    ltm_revenue: 1000.0, ltm_ebitda: 200.0, starting_cash: 50.0,
    starting_net_working_capital: 100.0, existing_debt: 100.0,
  },
  transaction: {
    close_date: '2026-01-01', entry_multiple: 10.0, exit_multiple: 10.0,
    hold_period_years: 5.0, transaction_fees: 50.0, seller_rollover: 100.0,
    minimum_cash: 25.0, cash_sweep_percent: 1.0,
  },
  projection: {
    default_drivers: {
      annual_revenue_growth: 0.08, gross_margin: 0.6, ebitda_margin: 0.2,
      da_percent_revenue: 0.03, capex_percent_revenue: 0.04,
      net_working_capital_percent_revenue: 0.1, cash_tax_rate: 0.25, base_rate: 0.04,
    },
    periods: [
      { label: 'Y1', months: 12 }, { label: 'Y2', months: 12 }, { label: 'Y3', months: 12 },
      { label: 'Y4', months: 12 }, { label: 'Y5', months: 12 },
    ],
  },
  debt_tranches: [
    { name: 'Revolver', tranche_type: 'revolver', initial_amount: 0.0, commitment: 150.0,
      spread: 0.03, cash_sweep_priority: 0 },
    { name: 'First Lien', tranche_type: 'term_loan', initial_amount: 800.0, senior: true,
      spread: 0.04, base_rate_floor: 0.05, annual_amortization_rate: 0.02,
      cash_sweep_priority: 10, oid_discount: 0.02, financing_fee_percent: 0.01 },
    { name: 'Mezzanine', tranche_type: 'mezzanine', initial_amount: 200.0, senior: false,
      spread: 0.08, pik_rate: 0.04, cash_sweep_priority: 20 },
  ],
  covenants: [
    { name: 'Total leverage', metric: 'total_leverage', test: 'maximum', threshold: 4.0 },
    { name: 'Interest coverage', metric: 'interest_coverage', test: 'minimum', threshold: 2.0 },
  ],
  valuation: { discount_rate: 0.1, terminal_growth_rate: 0.025, mid_year_convention: true },
};

function headers() {
  const h = { 'Content-Type': 'application/json' };
  if (TOKEN) h['Authorization'] = `Bearer ${TOKEN}`;
  return h;
}

export default function () {
  const h = headers();

  group('list', function () {
    const res = http.get(`${BASE_URL}/api/workspaces`, {
      headers: h, tags: { endpoint: 'list' },
    });
    check(res, { 'list 200': (r) => r.status === 200 });
  });

  if (!WORKSPACE_ID) {
    return; // per-workspace endpoints need a seeded WORKSPACE_ID (see perf/README.md)
  }

  group('overview', function () {
    const res = http.get(`${BASE_URL}/api/workspaces/${WORKSPACE_ID}`, {
      headers: h, tags: { endpoint: 'overview' },
    });
    check(res, { 'overview 200': (r) => r.status === 200 });
  });

  group('qa', function () {
    const res = http.post(
      `${BASE_URL}/api/workspaces/${WORKSPACE_ID}/qa`,
      JSON.stringify({ question: 'How concentrated is revenue in the largest customer?' }),
      { headers: h, tags: { endpoint: 'qa' } },
    );
    check(res, { 'qa 200': (r) => r.status === 200 });
  });

  group('search', function () {
    const res = http.get(
      `${BASE_URL}/api/workspaces/${WORKSPACE_ID}/search?q=revenue+growth+margin`,
      { headers: h, tags: { endpoint: 'search' } },
    );
    check(res, { 'search 200': (r) => r.status === 200 });
  });

  group('underwriting', function () {
    const res = http.post(
      `${BASE_URL}/api/workspaces/${WORKSPACE_ID}/underwriting/calculate`,
      JSON.stringify({ assumptions: ASSUMPTIONS }),
      { headers: h, tags: { endpoint: 'underwriting' } },
    );
    check(res, { 'underwriting 200': (r) => r.status === 200 });
  });
}
