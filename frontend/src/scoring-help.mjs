import { label } from "./format.mjs";

export const metricExplanations = {
  revenue_growth:
    "Revenue growth = revenue change ÷ prior revenue. Yahoo-reported growth; comparison period dates are not supplied.",
  earnings_growth:
    "Earnings growth = earnings change ÷ prior earnings. Yahoo-reported growth; comparison period dates are not supplied.",
  free_cash_flow_margin:
    "Free cash flow margin = free cash flow ÷ revenue. Uses Yahoo's latest reported totals; period dates are not supplied.",
  forward_pe:
    "Forward P/E = price ÷ forecast earnings per share. Yahoo's forward estimate; forecast period dates are not supplied.",
  trailing_pe:
    "Trailing P/E = price ÷ trailing earnings per share. Yahoo's trailing ratio; period dates are not supplied.",
  peg_ratio:
    "PEG = P/E ÷ expected earnings-growth rate (in percent). Yahoo's estimate; forecast horizon is not supplied.",
  price_to_sales:
    "Price/sales = market value ÷ trailing 12-month revenue. Yahoo-reported ratio.",
  free_cash_flow_yield:
    "Free cash flow yield = reported free cash flow ÷ market value. Latest Yahoo totals; cash-flow period dates are not supplied.",
  return_on_equity:
    "Return on equity = net income ÷ equity. Yahoo-reported ratio; period and averaging dates are not supplied.",
  profit_margin:
    "Profit margin = net income ÷ revenue. Yahoo-reported ratio; period dates are not supplied.",
  gross_margin:
    "Gross margin = gross profit ÷ revenue. Yahoo-reported ratio; period dates are not supplied.",
  debt_to_equity:
    "Debt/equity = total debt ÷ equity × 100. Yahoo percentage-point ratio; latest reported balance sheet, date not supplied.",
  current_ratio:
    "Current ratio = current assets ÷ current liabilities. Latest Yahoo-reported balance sheet; date not supplied.",
  momentum_1m:
    "1-month momentum = latest adjusted close ÷ adjusted close 21 trading sessions ago − 1.",
  momentum_3m:
    "3-month momentum = latest adjusted close ÷ adjusted close 63 trading sessions ago − 1.",
  momentum_6m:
    "6-month momentum = latest adjusted close ÷ adjusted close 126 trading sessions ago − 1.",
  momentum_12m:
    "12-month momentum = latest adjusted close ÷ adjusted close 252 trading sessions ago − 1.",
  volatility_3m:
    "3-month volatility = sample standard deviation of daily adjusted-close returns × √252. Uses 63 daily returns (64 trading-session prices).",
  max_drawdown_1y:
    "1-year drawdown = worst (adjusted close ÷ running peak − 1). Uses 253 trading-session prices (252 intervals).",
  market_cap:
    "Market value = price × shares outstanding. Latest Yahoo-reported value; timestamp not supplied.",
};

export function componentHelp(component, scoring = {}) {
  const weights = scoring[component] || {};
  const terms = Object.entries(weights).map(
    ([metric, weight]) =>
      `${Number((weight * 100).toFixed(2))}% × ${label(metric)} percentile`,
  );
  const formula = terms.length
    ? terms.join(" + ")
    : "weighted metric percentiles";
  const definitions = Object.keys(weights).map(
    (metric) =>
      metricExplanations[metric] || `${label(metric)}: formula unavailable.`,
  );
  return (
    `${label(component)} score = (${formula}) ÷ sum of available metric weights.\n` +
    "Each percentile compares this report's stocks on a 0–100 scale; tied values share a rank. Lower-is-better metrics are reversed. Missing metrics are omitted.\n" +
    definitions.join("\n")
  );
}

export function modelHelp(scoring = {}) {
  return (
    "Overall score = sum(category score × category weight × coverage) ÷ sum(category weight × coverage).\n" +
    "Metric percentile = 100 × average zero-based rank ÷ (valid peers − 1); reverse for lower-is-better metrics.\n\n" +
    ["growth", "valuation", "quality", "momentum", "risk"]
      .map((key) => componentHelp(key, scoring))
      .join("\n\n")
  );
}

export const overallHelp =
  "Overall score = sum(category score × category weight × coverage) ÷ sum(category weight × coverage).";

export function componentSummary(component, scoring = {}) {
  const windows = {
    growth:
      "Yahoo-reported growth and cash flow; comparison dates unavailable.",
    valuation:
      "Latest Yahoo ratios; forecast dates unavailable. Price/sales uses trailing 12 months.",
    quality:
      "Latest Yahoo profitability and balance-sheet ratios; period dates unavailable.",
    momentum:
      "Adjusted-price returns over 21, 63, 126 and 252 trading sessions (1, 3, 6 and 12 months).",
    risk: "Volatility: 63 daily returns. Drawdown: 252 trading intervals. Latest market value.",
  };
  const names = {
    momentum_1m: "1M",
    momentum_3m: "3M",
    momentum_6m: "6M",
    momentum_12m: "12M",
  };
  const weights = Object.entries(scoring[component] || {}).map(
    ([key, weight]) =>
      `${names[key] || label(key)} ${Number((weight * 100).toFixed(2))}%`,
  );
  return (
    "Weighted average of available metric percentiles (0–100).\n" +
    (component === "momentum" && weights.length
      ? weights.join(" · ") + "\n"
      : "") +
    (windows[component] || "Period unavailable.")
  );
}
