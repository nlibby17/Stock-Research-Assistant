import React, { useEffect, useRef, useState, useId } from "react";
import { createRoot } from "react-dom/client";
import Markdown from "react-markdown";
import {
  factors,
  number,
  percent,
  money,
  label,
  safeURL,
  pairOptions,
} from "./format.mjs";
import "./style.css";

const pages = [
  ["home", "Home", "home"],
  ["candidates", "Top Candidates", "chart"],
  ["comparison", "Stock Comparison", "VS"],
  ["research", "Research", "flask"],
  ["advanced", "Advanced", "Adv"],
];
function Icon({ name }) {
  const paths = {
    home: (
      <>
        <path d="m3 11 9-8 9 8" />
        <path d="M5 10v11h5v-7h4v7h5V10" />
      </>
    ),
    chart: (
      <>
        <path d="M3 3v18h18" />
        <path d="M7 17v-5m5 5V8m5 9V4" />
      </>
    ),
    flask: (
      <>
        <path d="M9 3h6m-5 0v7L4 19q-1 2 2 2h12q3 0 2-2l-6-9V3" />
        <path d="M7 15h10" />
      </>
    ),
  };
  return paths[name] ? (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  ) : (
    <span aria-hidden="true">{name}</span>
  );
}
function Value({ children }) {
  return children === "Unavailable" ? (
    <span className="missing">Unavailable</span>
  ) : (
    children
  );
}
function Notice({ children, tone = "" }) {
  return <div className={"notice " + tone}>{children}</div>;
}
function Heading({ eyebrow, children, description }) {
  return (
    <header className="section-heading">
      <span className="eyebrow">{eyebrow}</span>
      <h1>{children}</h1>
      {description && <p>{description}</p>}
    </header>
  );
}
function Metric({ title, value, help }) {
  return (
    <div className="metric" title={help}>
      <span>{title}</span>
      <strong>
        <Value>{value}</Value>
      </strong>
    </div>
  );
}
function DataTable({ rows, columns, caption }) {
  if (!rows?.length) return <p className="muted">No records available.</p>;
  const fields =
    columns || Object.keys(rows[0]).map((k) => ({ key: k, title: label(k) }));
  return (
    <div className="table-wrap">
      <table>
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {fields.map((f) => (
              <th key={f.key} scope="col">
                {f.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.ticker || i}>
              {fields.map((f) => (
                <td
                  key={f.key}
                  className={f.className || ""}
                  title={f.help ? f.help(row) : undefined}
                >
                  {f.render ? (
                    f.render(row)
                  ) : (
                    <Value>
                      {row[f.key] === null || row[f.key] === undefined
                        ? "Unavailable"
                        : typeof row[f.key] === "object"
                          ? JSON.stringify(row[f.key])
                          : String(row[f.key])}
                    </Value>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Json({ value }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
function Disclosure({ title, children, open = false }) {
  return (
    <details className="disclosure" open={open || undefined}>
      <summary>{title}</summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
function SourceLinks({ sources = [] }) {
  return (
    <ul className="sources">
      {sources.map((s, i) => (
        <li key={i}>
          {safeURL(s.url) ? (
            <a href={safeURL(s.url)} target="_blank" rel="noreferrer">
              {s.title || "Source"} ↗
            </a>
          ) : (
            <span>{s.title || "Source URL unavailable"}</span>
          )}
          <small>
            Published: {s.published_at || "Unknown"} · Event:{" "}
            {s.event_at || "Unknown"}
          </small>
        </li>
      ))}
    </ul>
  );
}
function ResearchMarkdown({ children }) {
  return (
    <Markdown
      skipHtml
      disallowedElements={["img"]}
      urlTransform={(url) => safeURL(url) || ""}
      components={{
        a: ({ href, children }) =>
          href ? (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ) : (
            <span>{children}</span>
          ),
      }}
    >
      {children}
    </Markdown>
  );
}
function ResearchText({ note }) {
  if (!note)
    return (
      <Notice>
        Current-source qualitative research has not been imported for this stock
        in this report.
      </Notice>
    );
  return (
    <div className="research-copy">
      {[
        ["Investment thesis", "thesis"],
        ["Bull case", "bull_case"],
        ["Bear case", "bear_case"],
        ["Valuation", "valuation"],
        ["Catalysts", "catalysts"],
        ["Risks", "risks"],
        ["What changed", "what_changed"],
      ].map(([name, key]) => (
        <section key={key}>
          <h3>{name}</h3>
          <ResearchMarkdown>
            {Array.isArray(note[key])
              ? note[key].join("\n")
              : note[key] || "Not provided"}
          </ResearchMarkdown>
        </section>
      ))}
    </div>
  );
}
function useVisible() {
  const ref = useRef(null),
    [visible, setVisible] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      { threshold: 0.18 },
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, visible];
}
function Bars({
  groups,
  series = ["Score"],
  colors = ["gold"],
  title = "Score comparison",
}) {
  const [ref, visible] = useVisible();
  const [tooltip, setTooltip] = useState(null);
  const tooltipId = useId();
  function showTooltip(event, group, value, seriesIndex) {
    const bounds = ref.current.getBoundingClientRect();
    const target = event.currentTarget.getBoundingClientRect();
    const pointerX = event.clientX || target.left + target.width / 2;
    const pointerY = event.clientY || target.top;
    setTooltip({
      group,
      value,
      seriesIndex,
      x: Math.max(12, Math.min(pointerX - bounds.left, bounds.width - 292)),
      y: Math.max(12, pointerY - bounds.top - 140),
    });
  }
  const width = Math.max(640, groups.length * 78),
    height = 305,
    plot = 220,
    bottom = 250,
    groupWidth = (width - 60) / Math.max(groups.length, 1);
  return (
    <figure
      ref={ref}
      className={"chart " + (visible ? "is-visible" : "")}
      aria-label={title}
      onPointerLeave={() => setTooltip(null)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setTooltip(null);
      }}
    >
      <div className="chart-scroll">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={v}>
              <line
                x1="42"
                x2={width - 10}
                y1={bottom - (v / 100) * plot}
                y2={bottom - (v / 100) * plot}
                className="grid-line"
              />
              <text
                x="32"
                y={bottom - (v / 100) * plot + 4}
                textAnchor="end"
                className="axis"
              >
                {v}
              </text>
            </g>
          ))}
          {groups.map((g, i) => (
            <g key={g.label}>
              {g.values.map((value, j) => {
                const barWidth = Math.min(42, groupWidth / (series.length + 1)),
                  x =
                    42 +
                    i * groupWidth +
                    groupWidth / 2 +
                    (j - (series.length - 1) / 2) * barWidth -
                    barWidth * 0.4;
                return (
                  <g
                    key={j}
                    tabIndex="0"
                    role="img"
                    aria-label={`${g.label} · ${series[j]}: ${number(value)}${g.coverage ? " · Coverage " + percent(g.coverage[j], 0) : ""}${g.help ? " · " + g.help : ""}`}
                    aria-describedby={
                      tooltip?.group === g && tooltip.seriesIndex === j
                        ? tooltipId
                        : undefined
                    }
                    onPointerMove={(event) => showTooltip(event, g, value, j)}
                    onPointerLeave={() => setTooltip(null)}
                    onFocus={(event) => showTooltip(event, g, value, j)}
                    onBlur={() => setTooltip(null)}
                  >
                    <rect
                      x={x - 4}
                      y="20"
                      width={barWidth * 0.8 + 8}
                      height={bottom - 10}
                      fill="transparent"
                    />
                    {value != null ? (
                      <rect
                        className={"bar " + colors[j]}
                        x={x}
                        y={
                          bottom -
                          (Math.max(0, Math.min(100, value)) / 100) * plot
                        }
                        width={barWidth * 0.8}
                        height={
                          (Math.max(0, Math.min(100, value)) / 100) * plot
                        }
                        rx="3"
                        style={{ animationDelay: `${i * 35 + j * 30}ms` }}
                      />
                    ) : (
                      <text
                        x={x + barWidth * 0.4}
                        y={bottom - 7}
                        textAnchor="middle"
                        className="axis"
                      >
                        N/A
                      </text>
                    )}
                    <text
                      x={x + barWidth * 0.4}
                      y={
                        value == null
                          ? bottom - 25
                          : bottom -
                            (Math.max(0, Math.min(100, value)) / 100) * plot -
                            7
                      }
                      textAnchor="middle"
                      className={"bar-label " + colors[j]}
                    >
                      {value == null ? "" : number(value)}
                    </text>
                  </g>
                );
              })}
              <text
                x={42 + i * groupWidth + groupWidth / 2}
                y={bottom + 25}
                textAnchor="middle"
                className="axis category"
              >
                {g.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
      {tooltip && (
        <div
          id={tooltipId}
          role="tooltip"
          className="chart-tooltip"
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <strong className={colors[tooltip.seriesIndex]}>
            {tooltip.group.label} · {series[tooltip.seriesIndex]}
          </strong>
          <span>Score: {number(tooltip.value)} / 100</span>
          {tooltip.group.coverage && (
            <span>
              Coverage:{" "}
              {percent(tooltip.group.coverage[tooltip.seriesIndex], 0)}
            </span>
          )}
          {tooltip.group.help && <p>{tooltip.group.help}</p>}
        </div>
      )}
      <figcaption>
        {series.map((s, i) => (
          <span key={s} className={colors[i]}>
            <i /> {s}
          </span>
        ))}
        <span>Relative score · 0–100</span>
      </figcaption>
      <Disclosure title="Chart data">
        <DataTable
          rows={groups.map((g) => ({
            Measure: g.label,
            ...Object.fromEntries(
              series.map((s, i) => [s, number(g.values[i])]),
            ),
          }))}
        />
      </Disclosure>
    </figure>
  );
}
function pointerTilt(e) {
  if (
    e.pointerType !== "mouse" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  )
    return;
  const b = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty(
    "--rx",
    `${(-(e.clientY - b.top - b.height / 2) / b.height) * 8}deg`,
  );
  e.currentTarget.style.setProperty(
    "--ry",
    `${((e.clientX - b.left - b.width / 2) / b.width) * 8}deg`,
  );
}
function resetTilt(e) {
  e.currentTarget.style.setProperty("--rx", "0deg");
  e.currentTarget.style.setProperty("--ry", "0deg");
}
function TiltCard({ children, className = "", onClick, title }) {
  const Tag = onClick ? "button" : "article";
  return (
    <Tag
      tabIndex={onClick ? undefined : 0}
      className={"tilt-card " + className}
      onClick={onClick}
      title={title}
      onPointerMove={pointerTilt}
      onPointerLeave={resetTilt}
    >
      {children}
    </Tag>
  );
}
function Home({ data, navigate }) {
  const r = data.run;
  return (
    <>
      <div className="hero">
        <div>
          <span className="eyebrow">Daily research brief</span>
          <h1>
            Personal Stock
            <br className="wide-break" /> Research Assistant
          </h1>
          <p>Analysis as of {r.as_of} · research and ranking only</p>
        </div>
        <div className="hero-meta">
          <span className="pill">{label(r.status)}</span>
          <span>
            {label(data.preferences.profile || "balanced")} ·{" "}
            {data.results.length} stocks
          </span>
          <small>
            Scoring {r.model_version} · App {data.app_version}
          </small>
        </div>
      </div>
      <p className="muted small">
        Keep the launching terminal open. Ctrl+C stops the server; closing the
        browser tab does not.
      </p>
      <section>
        <h2>Explore your report</h2>
        <div className="square-grid destinations">
          {pages.slice(1, 4).map(([id, name, icon], i) => (
            <TiltCard key={id} onClick={() => navigate(id)}>
              <Icon name={icon} />
              <span className="eyebrow">0{i + 1}</span>
              <h3>{id === "research" ? "Research Summary" : name}</h3>
              <span className="card-arrow" aria-hidden="true">
                ↗
              </span>
            </TiltCard>
          ))}
        </div>
      </section>
      <section>
        <h2>3-Month Sector Leaders</h2>
        <div className="square-grid">
          {data.sectors.map((s, i) => (
            <TiltCard
              key={s.sector}
              className="sector"
              title={"Usable companies: " + s.tickers.join(", ")}
            >
              <span className="eyebrow">#{i + 1} in this universe</span>
              <h3>{s.sector}</h3>
              <strong>{percent(s.median_return_3m)}</strong>
              <small>{s.member_count} usable companies</small>
              <span className="member-list">{s.tickers.join(" · ")}</span>
            </TiltCard>
          ))}
        </div>
        {!data.sectors.length && (
          <Notice>No sectors have sufficient stored evidence.</Notice>
        )}
        <p className="muted small">
          Median three-month stock return within this selected universe; sectors
          need at least three usable companies. This is not a whole-market
          sector ranking.
        </p>
      </section>
      <section>
        <h2>Market Overview</h2>
        <DataTable
          rows={data.market}
          columns={[
            { key: "ticker", title: "Ticker" },
            { key: "category", title: "Role" },
            {
              key: "price",
              title: "Price",
              render: (r) => <Value>{money(r.price)}</Value>,
            },
            { key: "price_as_of", title: "As of" },
            {
              key: "momentum_3m",
              title: "3M %",
              render: (r) => <Value>{percent(r.momentum_3m)}</Value>,
            },
            {
              key: "momentum_1m",
              title: "1M %",
              render: (r) => <Value>{percent(r.momentum_1m)}</Value>,
            },
          ]}
        />
        <p className="muted small">
          Sorted by three-month performance; unavailable returns appear last. 1M
          and 3M are trailing price returns.
        </p>
        {data.research?.market_overview?.summary && (
          <>
            <p className="prose">{data.research.market_overview.summary}</p>
            <SourceLinks sources={data.research.market_overview.sources} />
          </>
        )}
      </section>
      <div className="metrics home-metrics">
        <Metric title="Universe" value={data.results.length} />
        <Metric
          title="Eligible candidates"
          value={data.results.filter((r) => r.eligible).length}
        />
        <Metric title="Scoring model" value={r.model_version} />
        <Metric title="Provider" value={r.provider} />
        <Metric
          title="Ranking style"
          value={label(data.preferences.profile || "balanced")}
        />
      </div>
      <section>
        <button className="advanced-link" onClick={() => navigate("advanced")}>
          <span>
            <b>Adv</b> Advanced{" "}
            <small>Configuration, data quality & provenance</small>
          </span>
          <span>→</span>
        </button>
      </section>
    </>
  );
}
function Changes({ data }) {
  return (
    <section>
      <h2>What changed since the previous comparable report</h2>
      {!data.changes ? (
        <Notice>
          No earlier run passed the complete historical-comparison contract.{" "}
          {data.comparison_limitations.join("; ")}
        </Notice>
      ) : (
        <>
          <p className="muted">
            Compared with {data.previous.as_of} · same recorded calculation
            contract and exact universe membership. These are observed changes,
            not causal explanations.
          </p>
          {Object.entries(data.changes).map(([key, rows]) => (
            <Disclosure key={key} title={label(key)}>
              <DataTable rows={rows} />
            </Disclosure>
          ))}
        </>
      )}
    </section>
  );
}
function Candidates({ data }) {
  return (
    <>
      <Heading
        eyebrow="01 / Rankings"
        description="The highest eligible scores within your approved universe."
      >
        Top Candidates
      </Heading>
      <Bars
        title="Top candidate scores"
        groups={data.candidates.map((r) => ({
          label: r.ticker,
          values: [r.overall_score],
          coverage: [r.overall_coverage],
          help: r.score_breakdown,
        }))}
      />
      <h2>Candidate Score Comparison</h2>
      {!data.candidates.length ? (
        <Notice>{data.empty_candidates}</Notice>
      ) : (
        <DataTable
          rows={data.candidates}
          columns={[
            { key: "rank", title: "Rank" },
            { key: "ticker", title: "Ticker" },
            { key: "company", title: "Company" },
            { key: "sector", title: "Sector" },
            {
              key: "latest_price",
              title: "Price",
              render: (r) => money(r.latest_price),
            },
            {
              key: "overall_score",
              title: "Score",
              help: (r) => r.score_breakdown,
              render: (r) => number(r.overall_score),
            },
            {
              key: "overall_coverage",
              title: "Coverage",
              render: (r) => percent(r.overall_coverage, 0),
            },
            { key: "score_tier", title: "Score Tier", className: "violet" },
          ]}
        />
      )}
      <p className="muted small">
        Score combines available factors. Coverage is the share of weighted
        inputs available. Score tiers are relative to this universe.
      </p>
      <a
        className="button"
        href={"/api/rankings.csv?run=" + encodeURIComponent(data.run.run_id)}
      >
        Download all current rankings (CSV) ↓
      </a>
      <Changes data={data} />
    </>
  );
}
const metricFields = [
  ["Revenue growth", "revenue_growth", percent],
  ["Earnings growth", "earnings_growth", percent],
  ["Forward P/E", "forward_pe", number],
  ["Free cash flow yield", "free_cash_flow_yield", percent],
  ["Profit margin", "profit_margin", percent],
  ["Return on equity", "return_on_equity", percent],
  ["3-month price momentum", "momentum_3m", percent],
  ["Volatility (3-month, annualized)", "volatility_3m", percent],
];
function Comparison({ data }) {
  const [first, setFirst] = useState(data.candidates[0]?.ticker),
    [second, setSecond] = useState(data.candidates[1]?.ticker),
    [tab, setTab] = useState("scores");
  const selection = pairOptions(data.candidates, first, second),
    pair = [selection.left, selection.right].map((t) =>
      data.candidates.find((r) => r.ticker === t),
    );
  if (data.candidates.length < 2)
    return (
      <>
        <Heading eyebrow="02 / Side by side">Stock Comparison</Heading>
        <Notice>
          Comparison needs at least two eligible top candidates in this report.
        </Notice>
      </>
    );
  const notes = Object.fromEntries(
    (data.research?.companies || []).map((n) => [n.ticker, n]),
  );
  return (
    <>
      <Heading eyebrow="02 / Side by side">Stock Comparison</Heading>
      <div className="selectors">
        <label className="gold">
          First stock
          <select
            value={selection.left}
            onChange={(e) => setFirst(e.target.value)}
          >
            {data.candidates.map((r) => (
              <option key={r.ticker}>{r.ticker}</option>
            ))}
          </select>
        </label>
        <label className="violet">
          Second stock
          <select
            value={selection.right}
            onChange={(e) => setSecond(e.target.value)}
          >
            {selection.alternatives.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
      </div>
      <div
        className="arena tilt-only"
        key={selection.left + selection.right}
        onPointerMove={pointerTilt}
        onPointerLeave={resetTilt}
      >
        <div className="sheen" aria-hidden="true" />
        {pair.map((r, i) => (
          <React.Fragment key={r.ticker}>
            {i === 1 && <span className="vs-emblem">VS</span>}
            <article className={i ? "violet" : "gold"}>
              <h2>{r.ticker}</h2>
              <p>{r.company}</p>
              <small>{r.sector}</small>
              <strong>
                {number(r.overall_score)} <small>/ 100</small>
              </strong>
              <span>Rank #{r.rank}</span>
              <small>{percent(r.overall_coverage, 0)} coverage</small>
            </article>
          </React.Fragment>
        ))}
      </div>
      <p className="muted small">
        Report {data.run.as_of} · {data.run.provider} · model{" "}
        {data.run.model_version}. Scores are relative to this report’s universe;
        coverage shows available evidence.
      </p>
      <div className="tabs" role="tablist" aria-label="Comparison details">
        {[
          ["scores", "Score profile"],
          ["metrics", "Key metrics"],
          ["research", "Research & sources"],
        ].map(([id, title]) => (
          <button
            role="tab"
            aria-selected={tab === id}
            key={id}
            onClick={() => setTab(id)}
          >
            {title}
          </button>
        ))}
      </div>
      {tab === "scores" && (
        <>
          <Bars
            key="compare-factors"
            title="Component scores side by side"
            series={pair.map((r) => r.ticker)}
            colors={["gold", "violet"]}
            groups={factors.map((f) => ({
              label: label(f),
              values: pair.map((r) => r.component_scores[f]),
              coverage: pair.map((r) => r.component_coverage[f]),
            }))}
          />
          <div className="comparison-table">
            <DataTable
              rows={factors.map((f) => ({
                factor: label(f),
                left: pair[0].component_coverage[f],
                right: pair[1].component_coverage[f],
              }))}
              columns={[
                { key: "factor", title: "Coverage" },
                {
                  key: "left",
                  title: pair[0].ticker,
                  render: (r) => percent(r.left, 0),
                },
                {
                  key: "right",
                  title: pair[1].ticker,
                  render: (r) => percent(r.right, 0),
                },
              ]}
            />
          </div>
          <p className="muted small">
            Higher risk scores mean a more favorable risk profile. Missing
            scores have no bar; unavailable values are not zero.
          </p>
        </>
      )}
      {tab === "metrics" && (
        <>
          <div className="comparison-table">
            <DataTable
              rows={[
                {
                  measure: "Latest price",
                  left: money(pair[0].latest_price),
                  right: money(pair[1].latest_price),
                },
                {
                  measure: "Price date",
                  left: pair[0].price_as_of,
                  right: pair[1].price_as_of,
                },
                ...metricFields.map(([measure, key, format]) => ({
                  measure,
                  left: format(pair[0].metrics[key]),
                  right: format(pair[1].metrics[key]),
                })),
              ]}
              columns={[
                { key: "measure", title: "Measure" },
                { key: "left", title: pair[0].ticker, className: "gold" },
                { key: "right", title: pair[1].ticker, className: "violet" },
              ]}
            />
          </div>
          <p className="muted small">
            Stored report values. Price dates do not identify fundamental
            reporting periods.
          </p>
          {pair.map(
            (r) =>
              r.warnings.length > 0 && (
                <Notice key={r.ticker}>
                  {r.ticker}: {r.warnings.join("; ")}
                </Notice>
              ),
          )}
        </>
      )}
      {tab === "research" && (
        <div className="two-column">
          {pair.map((r) => (
            <section key={r.ticker}>
              <h2>{r.ticker}</h2>
              <ResearchText note={notes[r.ticker]} />
              <SourceLinks sources={notes[r.ticker]?.sources} />
            </section>
          ))}
        </div>
      )}
    </>
  );
}
function Research({ data }) {
  const [selected, setSelected] = useState(0),
    [tab, setTab] = useState("overview"),
    r = data.candidates[selected];
  const note = data.research?.companies?.find((n) => n.ticker === r?.ticker),
    filings = data.filings[r?.ticker];
  return (
    <>
      <Heading eyebrow="03 / Evidence & interpretation">
        Research Summary
      </Heading>
      <div
        className="research-numbers"
        role="tablist"
        aria-label="Ranked companies"
      >
        {data.candidates.map((r, i) => (
          <button
            key={r.ticker}
            role="tab"
            onPointerMove={pointerTilt}
            onPointerLeave={resetTilt}
            aria-selected={selected === i}
            title={`Rank ${r.rank}: ${r.ticker}`}
            aria-label={`Rank ${r.rank}: ${r.ticker}`}
            onClick={() => {
              setSelected(i);
              setTab("overview");
            }}
          >
            {i + 1}
            <small>{r.ticker}</small>
          </button>
        ))}
      </div>
      {!r ? (
        <Notice>{data.empty_candidates}</Notice>
      ) : (
        <section key={r.ticker} className="company-detail">
          <header className="company-title">
            <div>
              <span className="eyebrow">
                Rank #{r.rank} · {r.sector}
              </span>
              <h2>
                {r.ticker} <span>{r.company}</span>
              </h2>
            </div>
            <span className="pill violet tier-badge">
              {label(r.score_tier)}
            </span>
          </header>
          <div className="tabs" role="tablist" aria-label="Company details">
            {[
              ["overview", "Score overview"],
              ["research", "Research"],
              ["evidence", "Filings & sources"],
            ].map(([id, title]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
              >
                {title}
              </button>
            ))}
          </div>
          {tab === "overview" && (
            <>
              <div className="metrics">
                <Metric
                  title="Overall score"
                  value={number(r.overall_score)}
                  help={r.score_breakdown}
                />
                <Metric title="Price" value={money(r.latest_price)} />
                <Metric
                  title="Coverage"
                  value={percent(r.overall_coverage, 0)}
                />
                <Metric
                  title="Price as of"
                  value={r.price_as_of || "Unavailable"}
                />
              </div>
              <Bars
                groups={factors.map((f) => ({
                  label: label(f),
                  values: [r.component_scores[f]],
                  coverage: [r.component_coverage[f]],
                }))}
              />
              <DataTable
                rows={factors.map((f) => ({
                  factor: label(f),
                  score: number(r.component_scores[f]),
                  coverage: percent(r.component_coverage[f], 0),
                  status:
                    r.component_coverage[f] >= 1
                      ? "Complete"
                      : r.component_coverage[f] > 0
                        ? "Partial"
                        : "Unavailable",
                }))}
              />
              {r.warnings.length > 0 && (
                <Notice>{r.warnings.join("; ")}</Notice>
              )}
            </>
          )}
          {tab === "research" && <ResearchText note={note} />}
          {tab === "evidence" && (
            <>
              <h3>Latest SEC filings</h3>
              <p className="muted">
                {filings?.limitation ||
                  "SEC filings filtered to information available by this report’s completion time."}
              </p>
              {filings?.items.length ? (
                <ul className="sources">
                  {filings.items.map((f, i) => (
                    <li key={i}>
                      {safeURL(f.filing_index_url) ? (
                        <a
                          href={safeURL(f.filing_index_url)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {f.form} ↗
                        </a>
                      ) : (
                        f.form
                      )}
                      <small>
                        Period {f.report_date || "Unknown"} · Available{" "}
                        {f.accepted_at || f.availability_date || "Unknown"} (
                        {f.availability_precision || "date"})
                      </small>
                    </li>
                  ))}
                </ul>
              ) : (
                <Notice>
                  No qualifying SEC filing metadata is stored for this company.
                </Notice>
              )}
              <h3>Research sources</h3>
              <SourceLinks sources={note?.sources} />
              {!note?.sources?.length && (
                <p className="muted">
                  No qualitative research sources imported for this stock in
                  this report.
                </p>
              )}
            </>
          )}
        </section>
      )}
    </>
  );
}
function Shadow({ data }) {
  const r = data.report_shadow;
  return !r ? (
    <Notice>
      No provider comparison is linked to displayed analysis run{" "}
      {data.run.run_id}. Installation-current data was not substituted.
    </Notice>
  ) : (
    <>
      <p>
        Report-bound comparison {r.comparison_run_id} · {r.universe_name} ·{" "}
        {r.scope_count}/{r.universe_size} stocks · policy {r.config_version} ·
        completed {r.completed_at}
      </p>
      <Notice tone={r.evidence_qualified ? "" : "warning"}>
        {r.evidence_qualified
          ? `Qualifies for ${r.evidence_date}`
          : `Does not add promotion evidence: ${r.evidence_reason}`}
      </Notice>
      <Disclosure title="Stored SEC formula contracts">
        <Json value={r.formula_contracts} />
      </Disclosure>
      <div className="metrics">
        {Object.entries(data.shadow_counts).map(([k, v]) => (
          <Metric key={k} title={label(k)} value={v} />
        ))}
      </div>
      <h3>Classification by metric</h3>
      <DataTable rows={data.shadow_by_metric} />
      <h3>Classification by sector</h3>
      <DataTable rows={data.shadow_by_sector} />
      <h3>Material discrepancies</h3>
      <DataTable
        rows={data.shadow_rows.filter(
          (r) => r.classification === "materially_different",
        )}
        columns={[
          "ticker",
          "sector",
          "metric_name",
          "sec_value",
          "yahoo_value",
          "relative_difference",
          "sec_end_date",
          "period_alignment",
        ].map((key) => ({
          key,
          title: label(key),
          render: (r) =>
            key === "relative_difference"
              ? percent(r[key])
              : (r[key] ?? "Unavailable"),
        }))}
      />
      <Disclosure title="All stored comparison rows and fallback candidates">
        <DataTable rows={data.shadow_rows} />
      </Disclosure>
    </>
  );
}
function Advanced({ data }) {
  const f = data.freshness,
    q = data.scoring_quality,
    active = data.active;
  const tickers = [
    ...new Set([
      ...Object.keys(f.prices || {}),
      ...Object.keys(f.fundamentals || {}),
    ]),
  ].sort();
  return (
    <>
      <Heading eyebrow="04 / Configuration & provenance">Advanced</Heading>
      <Disclosure title="Personalize ranking and universe" open>
        <p>
          Change ranking style, horizon, risk tolerance, candidate thresholds,
          or universe.
        </p>
        <p>
          Active configuration: {label(active.profile)} profile ·{" "}
          {label(active.horizon)} horizon · {label(active.risk)} risk ·{" "}
          {active.universe_size} stocks
        </p>
        <div className="two-column">
          <section>
            <h3>Windows</h3>
            <pre>
              .\.venv\Scripts\stockrank.exe configure{"\n"}
              .\.venv\Scripts\stockrank.exe config-check --live
            </pre>
          </section>
          <section>
            <h3>macOS / Linux</h3>
            <pre>
              ./.venv/bin/stockrank configure{"\n"}./.venv/bin/stockrank
              config-check --live
            </pre>
          </section>
        </div>
        <p className="muted">
          Run configure first, then config-check. Personal settings stay on this
          computer. This dashboard does not activate universe changes.
        </p>
      </Disclosure>
      <Disclosure title="Advanced">
        <Disclosure title="Run details">
          <p>{data.freshness_label}</p>
          <p>
            Application {data.app_version} · report {data.run.run_id} ·{" "}
            {data.run.completed_at || "Completion cutoff unavailable"}
          </p>
          <p>Reproducibility: {data.run.reproducibility_status}</p>
          <Json
            value={{
              report: data.run,
              manifest: data.manifest,
              limitations: data.reproducibility_reasons,
            }}
          />
        </Disclosure>
        <h2>Data quality and diagnostics</h2>
        <Notice>
          SEC Company Facts and local calculations remain isolated from ranking
          inputs through shadow comparison and an explicit promotion decision.
        </Notice>
        {data.warnings.length ? (
          data.warnings.map((w, i) => <p key={i}>{w}</p>)
        ) : (
          <p>No run-level warnings recorded.</p>
        )}
        <Disclosure title="Scoring metric peer samples">
          <p>
            Percentiles are calculated only when the configured peer minimum is
            met.
          </p>
          <DataTable
            rows={Object.entries(q.metric_peer_counts || {}).map(
              ([metric, count]) => ({
                metric,
                usable_peers: count,
                minimum: q.minimum_metric_peer_count,
                status:
                  count >= q.minimum_metric_peer_count
                    ? "Eligible"
                    : "Withheld",
              }),
            )}
          />
        </Disclosure>
        <Disclosure title="Per-stock price and fundamental freshness">
          <DataTable
            rows={tickers.map((t) => ({
              ticker: t,
              price_status: f.prices?.[t]?.status,
              price_as_of: f.prices?.[t]?.price_as_of,
              price_age_hours: f.prices?.[t]?.age_hours,
              fundamental_status: f.fundamentals?.[t]?.status,
              fetched_at: f.fundamentals?.[t]?.fetched_at,
              fundamental_age_hours: f.fundamentals?.[t]?.age_hours,
            }))}
          />
          <Disclosure title="Stored continuity and freshness detail">
            <Json value={f} />
          </Disclosure>
        </Disclosure>
        <Disclosure title="Provider status and diagnostics">
          <Notice>
            Installation-current diagnostics—not report-bound evidence.
          </Notice>
          <p>
            Active universe {active.universe_name} ({active.universe_size}{" "}
            stocks) · {active.model_version} · {label(active.profile)} ·{" "}
            {label(active.horizon)} horizon · {label(active.risk)} risk
          </p>
          <p>
            Active policy {data.shadow_policy || "Unavailable"} · report-bound
            policy {data.report_shadow?.config_version || "No linked evidence"}
          </p>
          <p>
            {data.configuration_mismatches.length
              ? "Configuration differs from report: " +
                data.configuration_mismatches.join("; ")
              : "Active configuration matches the report."}
          </p>
          <div className="health-grid">
            {data.provider_health.map((h) => (
              <article key={h.provider} className="health">
                <h3>{label(h.provider)}</h3>
                <strong>{label(h.status)}</strong>
                <p>
                  {h.checked_at || "No stored health record"}
                  {h.latency_ms != null && ` · ${number(h.latency_ms, 0)} ms`}
                </p>
                <p>
                  {h.cache_hit
                    ? "Cache used"
                    : h.provider === "sec-financials" ||
                        h.provider === "provider-shadow"
                      ? "Local stored data"
                      : "Live request / stored check"}
                </p>
                <small>{h.detail}</small>
              </article>
            ))}
          </div>
          {data.shadow_policy_error ? (
            <Notice tone="warning">{data.shadow_policy_error}</Notice>
          ) : (
            <p>
              Current promotion evidence:{" "}
              {data.shadow_progress ?? "Unavailable"}/
              {data.shadow_required ?? "Unavailable"} distinct market-data
              dates.{" "}
              {data.current_shadow
                ? `Latest comparison ${data.current_shadow.comparison_run_id} · ${data.current_shadow.completed_at} · ${data.current_shadow.analysis_run_id === data.run.run_id ? "linked" : "not linked"} to displayed report.`
                : "No completed full-universe comparison is stored for this active policy and universe."}
            </p>
          )}
        </Disclosure>
        <Disclosure title="Step 2.4A SEC financial snapshots (not ranking inputs)">
          {!data.financial_cutoff_valid ? (
            <Notice>
              Snapshots withheld: this report has no timezone-aware completion
              cutoff. Current snapshots were not substituted.
            </Notice>
          ) : (
            <>
              <p>
                Report-bound SEC evidence · {data.run.run_id} · cutoff{" "}
                {data.run.completed_at} · {data.financials.length}/
                {data.results.length} members with stored snapshots. Missing
                members remain missing.
              </p>
              <DataTable
                rows={data.financials}
                columns={[
                  { key: "ticker", title: "Ticker" },
                  { key: "as_of", title: "Snapshot as of" },
                  { key: "formula_version", title: "Formula" },
                  {
                    key: "formula_manifest",
                    title: "Formula evidence",
                    render: (r) =>
                      r.formula_manifest?.fingerprint || "Legacy limited",
                  },
                  {
                    key: "coverage",
                    title: "Coverage",
                    render: (r) => percent(r.coverage, 0),
                  },
                  {
                    key: "revenue_ttm",
                    title: "TTM revenue",
                    render: (r) => money(r.revenue_ttm),
                  },
                  {
                    key: "revenue_growth",
                    title: "Annual revenue growth",
                    render: (r) => percent(r.revenue_growth),
                  },
                  {
                    key: "net_margin",
                    title: "TTM net margin",
                    render: (r) => percent(r.net_margin),
                  },
                ]}
              />
            </>
          )}
        </Disclosure>
        <Disclosure title="Step 2.4B SEC/Yahoo shadow comparison (not ranking inputs)">
          <Shadow data={data} />
        </Disclosure>
        <Disclosure title="Personal profile and scoring configuration used for this run">
          <Json
            value={{
              preferences: data.preferences,
              universe_name: data.run.universe_name,
              universe_size: data.results.length,
              scoring: data.scoring,
            }}
          />
        </Disclosure>
      </Disclosure>
    </>
  );
}
function App() {
  const [data, setData] = useState(null),
    [error, setError] = useState(""),
    [page, setPage] = useState("home"),
    [leaving, setLeaving] = useState(false),
    [menu, setMenu] = useState(false);
  const main = useRef(null),
    navigationTimer = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/report", { signal: controller.signal })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw Error(d.error || "Report unavailable");
        return d;
      })
      .then(setData)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => {
      controller.abort();
      clearTimeout(navigationTimer.current);
    };
  }, []);
  function navigate(next) {
    if (next === page) return;
    clearTimeout(navigationTimer.current);
    setLeaving(true);
    navigationTimer.current = setTimeout(
      () => {
        setPage(next);
        setLeaving(false);
        setMenu(false);
        window.scrollTo({ top: 0, behavior: "instant" });
        main.current?.focus();
      },
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 140,
    );
  }
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <nav
        className={"side-nav " + (menu ? "expanded" : "")}
        aria-label="Dashboard pages"
      >
        <button
          className="nav-toggle"
          onClick={() => setMenu(!menu)}
          aria-label="Expand navigation"
          aria-expanded={menu}
        >
          ☰
        </button>
        {pages.map(([id, name, icon]) => (
          <button
            key={id}
            onClick={() => navigate(id)}
            aria-label={name}
            aria-current={page === id ? "page" : undefined}
          >
            <span className="nav-icon">
              <Icon name={icon} />
            </span>
            <span className="nav-label">{name}</span>
          </button>
        ))}
      </nav>
      <main
        id="main"
        ref={main}
        tabIndex="-1"
        className={leaving ? "leaving" : ""}
      >
        {error ? (
          <>
            <Heading>Report unavailable</Heading>
            <Notice tone="warning">{error}</Notice>
            <button onClick={() => window.location.reload()}>Retry</button>
          </>
        ) : !data ? (
          <div className="loading">Loading your stored report…</div>
        ) : !data.run ? (
          <>
            <Heading>Personal Stock Research Assistant</Heading>
            <Notice>
              No run exists yet. Run stockrank run from the project directory.
            </Notice>
          </>
        ) : (
          <>
            {data.run.provider === "demo-synthetic" && (
              <Notice tone="warning">
                SYNTHETIC DEMO DATA — do not use for investment decisions
              </Notice>
            )}
            {data.configuration_mismatches.length > 0 && (
              <Notice>
                Saved report notice: active configuration differs (
                {data.configuration_mismatches.join("; ")}). Run stockrank
                morning to create an updated report.
              </Notice>
            )}
            {data.run.reproducibility_status !== "recorded" && (
              <Notice>
                Legacy report: limited reproducibility.{" "}
                {data.reproducibility_reasons.join("; ")}
              </Notice>
            )}
            {data.warnings.length > 0 && (
              <Notice tone="warning">
                {data.warnings.length} data-quality warnings recorded.{" "}
                <button
                  className="text-button"
                  onClick={() => navigate("advanced")}
                >
                  Review in Advanced →
                </button>
              </Notice>
            )}
            <div className="page" key={page}>
              {page === "home" ? (
                <Home data={data} navigate={navigate} />
              ) : page === "candidates" ? (
                <Candidates data={data} />
              ) : page === "comparison" ? (
                <Comparison data={data} />
              ) : page === "research" ? (
                <Research data={data} />
              ) : (
                <Advanced data={data} />
              )}
            </div>
            <footer>
              Research and ranking only. Scores are relative—not expected
              returns or certainty.
            </footer>
          </>
        )}
      </main>
    </>
  );
}
export { Home, Candidates, Comparison, Research, Advanced, Bars, ResearchText };
if (typeof document !== "undefined")
  createRoot(document.getElementById("root")).render(<App />);
