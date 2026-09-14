import test from "node:test";
import assert from "node:assert/strict";
import {
  componentHelp,
  modelHelp,
  componentSummary,
} from "../src/scoring-help.mjs";

test("explanations follow saved metric weights and disclose unknown growth dates", () => {
  const help = componentHelp("growth", {
    growth: { revenue_growth: 0.6, earnings_growth: 0.4 },
  });
  assert.match(help, /60%/);
  assert.match(help, /40%/);
  assert.match(help, /comparison period dates are not supplied/);
  assert.doesNotMatch(help, /year.over.year/i);
});
test("model explains coverage weighting and actual price windows", () => {
  const help = modelHelp({
    momentum: { momentum_1m: 0.5, momentum_12m: 0.5 },
    risk: { volatility_3m: 0.5, max_drawdown_1y: 0.5 },
  });
  for (const text of [
    "category weight × coverage",
    "21 trading sessions",
    "252 trading sessions",
    "63 daily returns",
    "253 trading-session prices",
  ])
    assert.ok(help.includes(text));
});

test("graph summaries are compact while full formulas remain available", () => {
  const scoring = { momentum: { momentum_1m: 0.2, momentum_3m: 0.8 } };
  for (const category of [
    "growth",
    "valuation",
    "quality",
    "momentum",
    "risk",
  ]) {
    const summary = componentSummary(category, scoring);
    assert.ok(summary.length < 310);
    assert.ok(summary.split("\n").length <= 3);
  }
  assert.match(componentSummary("momentum", scoring), /1M 20% · 3M 80%/);
  assert.match(componentHelp("momentum", scoring), /adjusted close/);
});
