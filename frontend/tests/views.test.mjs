import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Compile the actual views in memory. Fixtures below are only test inputs, never served.
const compiled = buildSync({
  entryPoints: [fileURLToPath(new URL("../src/app.jsx", import.meta.url))],
  bundle: true,
  write: false,
  platform: "node",
  format: "cjs",
  external: ["react", "react-dom"],
  loader: { ".css": "empty" },
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(
  createRequire(import.meta.url),
  module,
  module.exports,
);
const { Home, Candidates, Comparison, Research, Advanced, ResearchText } =
  module.exports;
const candidate = {
  ticker: "TEST",
  company: "Test <company>",
  sector: "Technology",
  rank: 1,
  eligible: true,
  overall_score: 70,
  overall_coverage: 1,
  latest_price: null,
  price_as_of: null,
  score_tier: "Top Tier",
  metrics: {},
  component_scores: { growth: 70 },
  component_coverage: { growth: 1 },
  warnings: [],
};
const data = {
  run: {
    run_id: "test-run",
    as_of: "2026-09-05",
    model_version: "test",
    provider: "test",
    status: "completed",
  },
  app_version: "test",
  results: [candidate],
  candidates: [candidate],
  preferences: {},
  active: {},
  scoring: {},
  filings: {},
  research: null,
  market: [],
  sectors: [],
  comparison_limitations: ["Test contract differs"],
  changes: null,
  configuration_mismatches: [],
  warnings: [],
  reproducibility_reasons: [],
  freshness: {},
  scoring_quality: {},
  provider_health: [],
  financials: [],
  financial_cutoff_valid: false,
  report_shadow: null,
  shadow_counts: {},
  shadow_rows: [],
  empty_candidates: "No eligible candidates under stored rules",
};
const render = (View, value = data) =>
  renderToStaticMarkup(
    React.createElement(View, { data: value, navigate: () => {} }),
  );
test("all migrated views render missing and legacy data honestly", () => {
  for (const View of [Home, Candidates, Comparison, Research, Advanced])
    assert.ok(render(View).length > 100);
  assert.match(render(Candidates), /Unavailable/);
  assert.match(render(Comparison), /at least two/);
  assert.match(render(Advanced), /Snapshots withheld/);
  assert.match(render(Advanced), /Installation-current diagnostics/);
  assert.match(render(Advanced), /Current snapshots were not substituted/);
});
test("empty candidates never become synthetic stocks", () => {
  const empty = { ...data, candidates: [] };
  assert.match(render(Research, empty), /No eligible candidates/);
  assert.doesNotMatch(render(Research, empty), /Test &lt;company&gt;/);
});
test("research initially shows only first real candidate and escapes content", () => {
  const two = {
    ...data,
    candidates: [
      candidate,
      { ...candidate, ticker: "NEXT", company: "Second company" },
    ],
    research: {
      companies: [
        { ticker: "TEST", thesis: "<script>danger</script>", sources: [] },
      ],
    },
  };
  const html = render(Research, two);
  assert.match(html, /Test &lt;company&gt;/);
  assert.doesNotMatch(html, /Second company/);
  assert.doesNotMatch(html, /<script>danger/);
  assert.match(html, /aria-label="Rank 1: TEST"/);
});
test("comparison uses vertical bars and distinct stock colors with rank labels", () => {
  const html = render(Comparison, {
    ...data,
    candidates: [candidate, { ...candidate, ticker: "NEXT", rank: 2 }],
  });
  assert.match(html, /Rank #1/);
  assert.match(html, /Rank #2/);
  assert.match(html, /class="bar gold"/);
  assert.match(html, /class="bar violet"/);
  assert.match(html, /100%/);
  assert.doesNotMatch(html, /Relative score rank/);
});

test("imported research keeps Markdown without active HTML or remote images", () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchText, {
      note: {
        thesis:
          "**Evidence** [source](https://www.sec.gov/) <script>alert(1)</script> ![tracking](https://example.com/tracking.png)",
        risks: "[unsafe](javascript:alert(1))",
      },
    }),
  );
  assert.match(html, /<strong>Evidence<\/strong>/);
  assert.match(html, /href="https:\/\/www.sec.gov\/"/);
  assert.doesNotMatch(html, /<script|<img|href="javascript:/);
});

test("Home section order keeps navigation first and status cards near Advanced", () => {
  const html = render(Home);
  const positions = [
    "Explore your report",
    "3-Month Sector Leaders",
    "Market Overview",
    "home-metrics",
    "advanced-link",
  ].map((text) => html.indexOf(text));
  assert.ok(
    positions.every((p, i) => p >= 0 && (i === 0 || p > positions[i - 1])),
  );
});

test("Top Candidates leads with the graph, then the named comparison table", () => {
  const html = render(Candidates);
  assert.ok(
    html.indexOf("<figure") <
      html.indexOf("<h2>Candidate Score Comparison</h2>"),
  );
  assert.ok(
    html.indexOf("<h2>Candidate Score Comparison</h2>") <
      html.indexOf('<th scope="col">Rank</th>'),
  );
  assert.doesNotMatch(html, /Entry requires/);
  assert.match(html, /aria-label="TEST · Score: 70.0 · Coverage 100%/);
});

test("Research has a larger title-case tier badge and comparison opts into tilt only", () => {
  assert.match(render(Research), /class="pill violet tier-badge">Top Tier/);
  assert.match(
    render(Comparison, {
      ...data,
      candidates: [candidate, { ...candidate, ticker: "NEXT" }],
    }),
    /class="arena tilt-only"/,
  );
});
