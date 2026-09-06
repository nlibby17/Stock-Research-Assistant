"""Presentation of two candidates from the same stored analysis run."""

from __future__ import annotations

import html
import math
from urllib.parse import urlsplit

import streamlit as st


def _number(value, spec: str = ".1f", suffix: str = "") -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "Unavailable"
    return format(value, spec) + suffix


def _score_bar(value) -> str:
    label = _number(value)
    if label == "Unavailable":
        return '<span class="sr-missing">Unavailable</span>'
    width = max(0, min(100, value))
    return (
        f'<div class="vs-component"><span>{label}</span>'
        f'<div class="vs-component-track" aria-hidden="true"><i style="width:{width:.1f}%"></i>'
        "</div></div>"
    )


def render_stock_vs(candidates: list[dict], research: dict, run: dict) -> None:
    """Render only supplied stored values; never load or rescore securities."""
    st.markdown('<div id="stock-vs" style="scroll-margin-top:5rem"></div>', unsafe_allow_html=True)
    with st.container(key="stock_vs_panel"), st.expander("Stock head-to-head", expanded=False):
        _render_comparison(candidates, research, run)
    # Anchor navigation is client-side: expand without a reload or losing selections.
    # Older supported installations predate st.iframe; only they need the legacy API.
    embed = getattr(st, "iframe", None)
    if embed is None:
        from streamlit.components.v1 import html as embed
    embed(
        """<script>
        const host = window.parent;
        const doc = host.document;
        function sweep(panel) {
          const light = panel.querySelector('.vs-light');
          if (!light || host.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
          light.getAnimations().forEach(animation => animation.cancel());
          light.animate([
            {transform:'translate(-35%,-35%)', opacity:0},
            {opacity:1, offset:0.25},
            {transform:'translate(35%,35%)', opacity:0}
          ], {duration:2400, easing:'ease-out'});
        }
        function isExpanded(panel) {
          return panel.open && !panel.querySelector('[data-testid="stExpanderDetails"][inert]');
        }
        if (host.stockVSToggle) doc.removeEventListener('toggle', host.stockVSToggle, true);
        if (host.stockVSObserver) host.stockVSObserver.disconnect();
        const states = new WeakMap();
        function checkExpansion() {
          const panel = doc.querySelector('.st-key-stock_vs_panel details');
          if (!panel) return;
          const expanded = isExpanded(panel);
          if (expanded && !states.get(panel)) requestAnimationFrame(() => sweep(panel));
          states.set(panel, expanded);
        }
        host.stockVSObserver = new host.MutationObserver(checkExpansion);
        const panelRoot = doc.querySelector('.st-key-stock_vs_panel');
        if (panelRoot) host.stockVSObserver.observe(panelRoot, {
          subtree:true, childList:true, attributes:true, attributeFilter:['open','inert']
        });
        checkExpansion();
        function openVS() {
          const panel = doc.querySelector('.st-key-stock_vs_panel details');
          if (!panel) return;
          if (!isExpanded(panel)) panel.querySelector('summary').click();
          requestAnimationFrame(() => doc.getElementById('stock-vs')?.scrollIntoView());
        }
        if (host.stockVSNavigation) doc.removeEventListener('click', host.stockVSNavigation);
        host.stockVSNavigation = event => {
          if (event.target.closest('a[href="#stock-vs"]')) openVS();
        };
        doc.addEventListener('click', host.stockVSNavigation);
        if (host.stockVSSelector) {
          doc.removeEventListener('pointerdown', host.stockVSSelector, true);
          doc.removeEventListener('focusin', host.stockVSSelector, true);
        }
        host.stockVSSelector = event => {
          if (event.target.closest('.st-key-vs_first_selector')) doc.body.dataset.vsSelector = 'first';
          if (event.target.closest('.st-key-vs_second_selector')) doc.body.dataset.vsSelector = 'second';
        };
        doc.addEventListener('pointerdown', host.stockVSSelector, true);
        doc.addEventListener('focusin', host.stockVSSelector, true);
        if (host.stockSectionScroll) doc.removeEventListener('scroll', host.stockSectionScroll, true);
        if (host.stockSectionResize) host.removeEventListener('resize', host.stockSectionResize);
        let pending = false;
        function updateSection() {
          pending = false;
          const links = [...doc.querySelectorAll('.sr-nav a')];
          let current = links[0];
          for (const link of links) {
            const target = doc.getElementById(link.hash.slice(1));
            if (target && target.getBoundingClientRect().top <= host.innerHeight * .35) current = link;
          }
          links.forEach(link => {
            if (link === current) link.setAttribute('aria-current', 'location');
            else link.removeAttribute('aria-current');
          });
        }
        host.stockSectionScroll = () => {
          if (!pending) { pending = true; requestAnimationFrame(updateSection); }
        };
        host.stockSectionResize = host.stockSectionScroll;
        doc.addEventListener('scroll', host.stockSectionScroll, true);
        host.addEventListener('resize', host.stockSectionResize);
        updateSection();
        if (host.location.hash === '#stock-vs') openVS();
        </script>""",
        height=1,
    )


def _render_comparison(candidates: list[dict], research: dict, run: dict) -> None:
    if len(candidates) < 2:
        st.info("VS needs at least two eligible top candidates in this report.")
        return

    st.markdown(
        """<style>
        #stock-vs {scroll-margin-top:5rem}
        .st-key-stock_vs_panel details {border:1px solid #807595 !important;
          border-radius:14px !important;overflow:hidden;
          box-shadow:0 0 22px rgba(189,176,237,.09)}
        .st-key-stock_vs_panel summary {padding:20px 22px !important;
          background:linear-gradient(115deg,rgba(231,201,127,.14),#1b2639 45%,rgba(189,176,237,.19));
          border-left:4px solid #e7c97f;min-height:76px}
        .st-key-stock_vs_panel summary p {font-size:1.35rem !important;
          font-weight:700 !important;letter-spacing:.01em;color:#f1f5fa !important}
        .st-key-stock_vs_panel summary:hover {filter:brightness(1.15)}
        .st-key-stock_vs_panel summary:focus-visible {outline:2px solid #bdb0ed;outline-offset:-3px}
        .vs-arena {position:relative;overflow:hidden;border:1px solid #45506a;
          border-radius:16px;background:linear-gradient(135deg,#1e2b40,#141e2e 55%,#1b2639);
          padding:28px;margin:8px 0 20px;isolation:isolate}
        .vs-light {position:absolute;inset:-100%;pointer-events:none;
          background:linear-gradient(135deg,transparent 43%,rgba(241,220,162,.24) 49%,
          rgba(189,176,237,.12) 53%,transparent 58%);transform:translate(-35%,-35%);
          z-index:0;opacity:0}
        .vs-grid {position:relative;z-index:1;display:grid;grid-template-columns:minmax(0,1fr) 55px minmax(0,1fr);
          gap:18px;align-items:center}
        .vs-side {min-width:0}.vs-side:last-child{text-align:right}
        .vs-ticker {font-size:clamp(1.7rem,4vw,2.8rem);font-weight:750;
          letter-spacing:-.045em;color:#e7c97f;line-height:1.2}
        .vs-side:last-child .vs-ticker{color:#bdb0ed}
        .vs-company {color:#c9d3e1;font-size:.9rem;margin-top:6px;overflow-wrap:anywhere}
        .vs-sector {color:#94a6bd;font-size:.78rem;margin-top:4px}
        .vs-emblem {border:1px solid #526079;border-radius:50%;width:48px;height:48px;
          display:grid;place-items:center;color:#c9d3e1;font-size:.8rem;letter-spacing:.1em}
        .vs-score {font-size:2rem;font-weight:650;margin-top:18px;color:#f1f5fa}
        .vs-score small{font-size:.8rem;font-weight:400;color:#94a6bd}
        .vs-meta {color:#a9b7ca;font-size:.8rem;margin-top:4px}
        .vs-matrix {width:100%;table-layout:fixed;border-collapse:collapse;font-size:.9rem}
        .vs-matrix th,.vs-matrix td {padding:13px 10px;border-bottom:1px solid #293750;
          text-align:right;font-variant-numeric:tabular-nums}
        .vs-matrix th:first-child,.vs-matrix td:first-child{width:20%;text-align:left;
          color:#a9b7ca;overflow-wrap:anywhere}
        .vs-matrix th:nth-child(2),.vs-matrix td:nth-child(2){color:#e7c97f}
        .vs-matrix th:nth-child(3),.vs-matrix td:nth-child(3){color:#bdb0ed}
        .vs-matrix th:nth-child(2){background:rgba(231,201,127,.10)}
        .vs-matrix th:nth-child(3){background:rgba(189,176,237,.10)}
        .vs-matrix tbody tr:hover{background:rgba(189,176,237,.08)}
        .vs-matrix small{display:block;color:#f1f5fa;font-size:.75rem}
        .vs-component-track {height:4px;border-radius:4px;background:#293750;
          margin:7px 0 5px;overflow:hidden}
        .vs-component-track i {display:block;height:100%;background:currentColor;border-radius:4px}
        [role="option"][aria-selected="true"], [role="option"]:hover,
        [role="option"][data-highlighted="true"] {background:#bdb0ed !important;color:#141e2e !important}
        [data-testid="stSelectbox"] div:focus-within {
          border-color:#bdb0ed !important}
        .st-key-vs_first_selector [data-testid="stSelectbox"] div:focus-within {
          border-color:#e7c97f !important}
        body[data-vs-selector="first"] [role="option"][aria-selected="true"],
        body[data-vs-selector="first"] [role="option"]:hover,
        body[data-vs-selector="first"] [role="option"][data-highlighted="true"] {
          background:#e7c97f !important;color:#141e2e !important}
        @media(max-width:600px){.vs-arena{padding:16px}.vs-grid{gap:8px;
          grid-template-columns:minmax(0,1fr) 36px minmax(0,1fr)}
          .vs-emblem{width:34px;height:34px}.vs-score{font-size:1.5rem}
          .vs-matrix th,.vs-matrix td{padding:10px 5px;font-size:.8rem}}
        </style>""",
        unsafe_allow_html=True,
    )
    by_ticker = {row["ticker"]: row for row in candidates}
    tickers = list(by_ticker)
    # Explicitly reset stale widget values when another report changes the candidate set.
    if st.session_state.get("vs_left") not in tickers:
        st.session_state["vs_left"] = tickers[0]
    left_column, right_column = st.columns(2)
    with left_column, st.container(key="vs_first_selector"):
        left = st.selectbox("First stock", tickers, key="vs_left")
    alternatives = [ticker for ticker in tickers if ticker != left]
    if st.session_state.get("vs_right") not in alternatives:
        st.session_state["vs_right"] = alternatives[0]
    with right_column, st.container(key="vs_second_selector"):
        right = st.selectbox("Second stock", alternatives, key="vs_right")
    pair = [by_ticker[left], by_ticker[right]]
    escape = lambda value: html.escape(str(value))
    cards = []
    for row in pair:
        cards.append(
            '<div class="vs-side">'
            f'<div class="vs-ticker">{escape(row["ticker"])}</div>'
            f'<div class="vs-company">{escape(row["company"])}</div>'
            f'<div class="vs-sector">{escape(row["sector"])}</div>'
            f'<div class="vs-score">{_number(row.get("overall_score"))} '
            "<small>/ 100</small></div>"
            f'<div class="vs-meta">Relative score · rank {escape(row.get("rank") or "—")}</div>'
            f'<div class="vs-meta">{_number(row.get("overall_coverage"), ".0%")}'
            " coverage</div></div>"
        )
    st.markdown(
        '<div class="vs-arena"><div class="vs-light" aria-hidden="true"></div><div class="vs-grid">'
        + cards[0]
        + '<div class="vs-emblem">VS</div>'
        + cards[1]
        + "</div></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Report {run['as_of']} · {run['provider']} · model {run['model_version']}. "
        "Scores are relative to this report's universe; coverage shows available evidence."
    )

    def table(rows):
        missing = '<span class="sr-missing">Unavailable</span>'
        body = "".join(
            "<tr>"
            + "".join(f"<td>{missing if value == 'Unavailable' else value}</td>" for value in row)
            + "</tr>"
            for row in rows
        )
        st.markdown(
            '<table class="vs-matrix"><thead><tr><th scope="col">Measure</th>'
            f'<th scope="col">{escape(left)}</th><th scope="col">{escape(right)}</th>'
            "</tr></thead><tbody>" + body + "</tbody></table>",
            unsafe_allow_html=True,
        )

    factors, metrics, notes = st.tabs(["Score profile", "Key metrics", "Research & sources"])
    with factors:
        table(
            [
                [label]
                + [
                    _score_bar(row.get("component_scores", {}).get(key))
                    + "<small>"
                    + _number(row.get("component_coverage", {}).get(key), ".0%")
                    + " coverage</small>"
                    for row in pair
                ]
                for key, label in (
                    ("growth", "Growth"),
                    ("valuation", "Valuation"),
                    ("quality", "Quality"),
                    ("momentum", "Momentum"),
                    ("risk", "Risk"),
                )
            ]
        )
        st.caption(
            "Component scores use the same 0–100 scale. Higher risk scores mean a more favorable risk profile."
        )
    with metrics:
        table(
            [
                ["Latest price"]
                + [_number(row.get("latest_price"), ",.2f", " USD") for row in pair],
                ["Price date"] + [escape(row.get("price_as_of") or "Unavailable") for row in pair],
                *[
                    [label]
                    + [_number(row.get("metrics", {}).get(key), spec, suffix) for row in pair]
                    for key, label, spec, suffix in (
                        ("revenue_growth", "Revenue growth", ".1%", ""),
                        ("earnings_growth", "Earnings growth", ".1%", ""),
                        ("forward_pe", "Forward P/E", ".1f", "×"),
                        ("free_cash_flow_yield", "Free cash flow yield", ".1%", ""),
                        ("profit_margin", "Profit margin", ".1%", ""),
                        ("return_on_equity", "Return on equity", ".1%", ""),
                        ("momentum_3m", "3-month price momentum", ".1%", ""),
                        ("volatility_3m", "Volatility (3-month, annualized)", ".1%", ""),
                    )
                ],
            ]
        )
        st.caption(
            "Stored report values. Unavailable values are not zero. Price dates do not identify fundamental reporting periods."
        )
        for row in pair:
            if row.get("warnings"):
                st.caption(row["ticker"] + " data notes: " + "; ".join(row["warnings"]))
    with notes:
        for column, row in zip(st.columns(2), pair):
            with column:
                st.subheader(row["ticker"])
                note = research.get(row["ticker"])
                if not note:
                    st.info("Research has not been imported for this stock in this report.")
                    continue
                for label, field in (
                    ("Thesis", "thesis"),
                    ("Bull case", "bull_case"),
                    ("Bear case", "bear_case"),
                    ("Catalysts", "catalysts"),
                    ("Risks", "risks"),
                ):
                    st.markdown(f"**{label}**")
                    st.write(str(note.get(field) or "Not provided").replace("$", r"\$"))
                for source in note.get("sources", []):
                    url = str(source.get("url") or "")
                    if urlsplit(url).scheme in ("http", "https"):
                        st.link_button(str(source.get("title") or "Source"), url)
                        st.caption(
                            f"Published: {source.get('published_at') or 'Unknown'} · "
                            f"Event: {source.get('event_at') or 'Unknown'}"
                        )
