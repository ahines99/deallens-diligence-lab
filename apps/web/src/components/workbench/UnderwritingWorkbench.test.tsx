import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActorProvider } from "@/components/identity/ActorContext";
import type { ProjectionPeriodResult, UnderwritingCaseVersion } from "@/lib/types";
import { UnderwritingWorkbench } from "./UnderwritingWorkbench";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

const projection: ProjectionPeriodResult = {
  label: "Y5", start_date: "2030-01-01", end_date: "2030-12-31", months: 12,
  year_fraction: 1, revenue: 100, annualized_revenue: 100, revenue_growth: 0.08,
  gross_profit: 60, ebitda: 20, ebitda_margin: 0.2, cash_interest: 2,
  pik_interest: 0, cash_taxes: 3, net_income: 10, change_in_net_working_capital: 1,
  capex: 4, fcff: 12, ending_cash: 10, liquidity_shortfall: 0, total_debt: 20,
  net_debt: 10, total_leverage: 1, senior_leverage: 1, interest_coverage: 10,
  fixed_charge_coverage: 5, liquidity: 10, debt_tranches: [], covenants: [],
};

const savedCase: UnderwritingCaseVersion = {
  id: "case-1", workspace_id: "workspace-1", case_key: "base", label: "Base case",
  version: 1, parent_version_id: null, schema_version: "1", input_hash: "input-hash",
  output_hash: "output-hash", created_by: "demo-associate", change_note: "Initial case",
  created_at: "2026-07-13T12:00:00Z", latest_decision: null,
  assumptions: {
    currency: "USD",
    historical: { ltm_revenue: 100, ltm_ebitda: 20, starting_cash: 5, starting_net_working_capital: 10, existing_debt: 0 },
    transaction: { close_date: "2026-08-01", entry_multiple: 10, exit_multiple: 10, hold_period_years: 5, transaction_fees: 1, management_options_cashout: 0, other_uses: 0, seller_rollover: 0, minimum_cash: 5, cash_sweep_percent: 1 },
    projection: { default_drivers: { annual_revenue_growth: 0.08, gross_margin: 0.6, ebitda_margin: 0.2, da_percent_revenue: 0.03, capex_percent_revenue: 0.04, net_working_capital_percent_revenue: 0.1, cash_tax_rate: 0.25, base_rate: 0.04 }, periods: [] },
    debt_tranches: [], covenants: [],
    valuation: { discount_rate: 0.12, terminal_growth_rate: 0.025, mid_year_convention: true },
  },
  result: {
    currency: "USD",
    sources_uses: { entry_enterprise_value: 200, equity_purchase_price: 200, uses: [], sources: [], total_uses: 200, total_sources: 200, sponsor_equity: 200, rollover_equity: 0, sponsor_ownership: 1, balanced: true },
    projection: [projection],
    dcf: { discount_rate: 0.12, terminal_growth_rate: 0.025, pv_explicit_fcff: 50, terminal_value: 150, pv_terminal_value: 100, enterprise_value: 150, net_debt: 10, equity_value: 140, terminal_value_percent: 0.67 },
    returns: { exit_enterprise_value: 250, exit_debt: 20, exit_cash: 10, exit_equity_value: 240, sponsor_exit_proceeds: 240, sponsor_invested_capital: 200, moic: 1.2, xirr: 0.04, cash_flows: [] },
    summary: { revenue_cagr: 0.08, exit_ebitda: 20, exit_ebitda_margin: 0.2, minimum_liquidity: 5, maximum_total_leverage: 1, first_covenant_breach: null, first_debt_service_default: null },
    generated_at: "2026-07-13T12:00:00Z",
  },
};

describe("UnderwritingWorkbench input governance", () => {
  it("hides saved results immediately when a model input changes", () => {
    render(<ActorProvider><UnderwritingWorkbench workspaceId="workspace-1" cases={[savedCase]} /></ActorProvider>);
    expect(screen.getByText(/Version 1/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Change note"), { target: { value: "Documentation only" } });
    expect(screen.getByText(/Version 1/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("LTM revenue"), { target: { value: "110" } });
    expect(screen.getByText("Model inputs changed — results hidden")).toBeInTheDocument();
    expect(screen.queryByText(/Version 1/)).not.toBeInTheDocument();
  });
});
