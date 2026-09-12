"""Proposal rendering shared by saved copies and the interactive local review."""

from __future__ import annotations

from html import escape

from stockrank.universe_discovery import MAX_UNIVERSE_SIZE, PROFILE_LABELS


def render_review(
    proposal: dict,
    *,
    action_token: str | None = None,
    decision: str | None = None,
    considered=None,
    receipt=None,
) -> str:
    e = lambda value: escape(str(value), quote=True)
    candidates = {r["ticker"]: r for r in proposal["evidence"]["candidates"]}
    active = {m["ticker"] for m in proposal["base_members"]}
    editable = bool(action_token and not decision)
    sections = []
    for profile, result in proposal["profiles"].items():
        rows = []
        for member in result["members"]:
            row = candidates[member["ticker"]]
            status = "Add" if row["ticker"] in result["additions"] else "Keep"
            warnings = " · ".join(row["warnings"]) or "No additional candidate warnings"
            if row["exclusions"]:
                warnings = (
                    "Kept by override despite: " + "; ".join(row["exclusions"]) + " · " + warnings
                )
            liquidity = (
                f"${row['median_dollar_volume_20d']:,.0f}"
                if row.get("median_dollar_volume_20d") is not None
                else "Unavailable"
            )
            controls = ""
            if editable:
                controls = f'<label><input type="checkbox" form="edit-{profile}" name="remove" value="{e(row["ticker"])}"> Remove from this selection</label>'
                if row["ticker"] in active:
                    checked = " checked" if row["ticker"] in result.get("protected", []) else ""
                    controls += f'<br><label><input type="checkbox" form="edit-{profile}" name="protect" value="{e(row["ticker"])}"{checked}> Keep in future reviews</label>'
            score = f"{row['score']:.1f}" if row.get("score") is not None else "Unavailable"
            coverage = (
                f"{row['coverage']:.0%}" if row.get("coverage") is not None else "Unavailable"
            )
            rows.append(
                f'<tr><td><span class="{status.lower()}">{status}</span></td>'
                f"<td><strong>{e(row['ticker'])}</strong><small>{e(row['company'])}</small></td>"
                f"<td>{e(row['sector'])}</td><td>{score}</td>"
                f"<td>{coverage}</td><td>{e(row.get('price_as_of', ''))}</td>"
                f"<td><details><summary>Evidence</summary><p>{e(warnings)}</p>"
                f"<p>CIK: {e(row['cik'])} · Median dollar volume: "
                f"{liquidity}</p>"
                f"<p>Components: {e(row.get('component_scores', {}))}</p>"
                f'<a href="https://www.sec.gov/edgar/browse/?CIK={e(row["cik"])}" '
                f'target="_blank" rel="noopener">SEC filing history</a></details>{controls}</td></tr>'
            )
        removed = (
            "".join(
                f'<li><span class="remove">Remove</span> <strong>{e(r["ticker"])}</strong> · {e(r.get("sector", ""))} · '
                f"Pool score: {e(round(r['score'], 1) if r.get('score') is not None else 'unavailable')} "
                f"— {e('; '.join(r['reasons']))}"
                + (
                    f'<br><label><input type="checkbox" form="edit-{profile}" name="protect" value="{e(r["ticker"])}"> Keep this stock now and in future reviews</label>'
                    if editable
                    else ""
                )
                + "</li>"
                for r in result["removals"]
            )
            or "<li>No removals</li>"
        )
        bars = "".join(
            f'<div class="sector"><span>{e(sector)}</span><meter min="0" max="{proposal["policy"]["target_size"]}" '
            f'value="{count}">{count}</meter><b>{count}</b></div>'
            for sector, count in result["sector_counts"].items()
        )
        blockers = "".join(f"<li>{e(b)}</li>" for b in result["blockers"])
        gate = (
            (
                '<div class="blocked"><strong>Needs attention — activation blocked</strong><ul>'
                + blockers
                + "</ul></div>"
            )
            if blockers
            else ('<p class="ready">Ready for your review. Approval is still required.</p>')
        )
        if decision:
            gate = '<p class="ready">This proposal is closed. Your decision has been saved.</p>'
        description = (
            "Highest available scores in the screened pool, without sector quotas. Sector concentration is possible."
            if profile == "best_overall"
            else "Selects from the least-represented available sector first, then by score. All 11 sectors are required."
        )
        actions = ""
        if action_token and not decision:
            disabled = " disabled" if result["blockers"] else ""
            actions = f'''<form id="edit-{profile}" method="post" action="/confirm" class="actions">
<input type="hidden" name="token" value="{e(action_token)}">
<input type="hidden" name="profile" value="{profile}">
<button name="action" value="approve"{disabled}>Review &amp; approve {PROFILE_LABELS[profile]}</button>
<button name="action" value="reject" class="secondary">Reject this proposal (both profiles)</button>
<p>Use the stock checkboxes below to customize this profile, then review the exact list and total before confirming.
Kept stocks can grow your list up to {MAX_UNIVERSE_SIZE}. At the cap, you can remove stocks manually or request a suggested trim.</p></form>'''
        additional = ""
        if editable:
            selected_tickers = {m["ticker"] for m in result["members"]}
            choices = "".join(
                f'<li><label><input type="checkbox" form="edit-{profile}" name="include" value="{e(t)}"> Include {e(t)} · {e(r["company"])} · {e(r["sector"])} · score {r["score"]:.1f}</label></li>'
                for t, r in candidates.items()
                if t not in selected_tickers and not r["exclusions"] and r.get("score") is not None
            )
            additional = f"<details><summary>Other eligible stocks you can include</summary><ul>{choices or '<li>No other eligible candidates in this screen.</li>'}</ul></details>"
        sections.append(f'''
<section id="{profile}"><h2>{PROFILE_LABELS[profile]}</h2><p>{description}</p>
<div class="stats"><div><b>{len(result["members"])}</b>companies</div>
<div><b>{len(result["additions"])}</b>additions</div><div><b>{len(result["removals"])}</b>removals</div>
<div><b>{len(result["sector_counts"])}</b>sectors</div></div>{gate}
{actions}
<div class="removals"><h3>Proposed removals ({len(result["removals"])})</h3>
<p>These current members would leave your universe if you approve this profile.</p><ul>{removed}</ul></div>
<details class="distribution"><summary>Sector distribution</summary>{bars}</details>
<h3>Proposed universe: additions and retained companies</h3>
<div class="table"><table><thead><tr><th>Change</th><th>Company</th><th>Sector</th>
<th>Pool score</th><th>Coverage</th><th>Price date</th><th>Details</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
{additional}
</section>''')
    warnings = "".join(f"<li>{e(w)}</li>" for w in proposal["warnings"])
    excluded = "".join(
        f"<tr><td>{e(r['ticker'])}</td><td>{e('; '.join(r['exclusions']))}</td></tr>"
        for r in candidates.values()
        if r["exclusions"]
    )
    notice = (
        f"Decision recorded: {e(decision)}. Both profiles are closed for this proposal."
        if decision
        else "Use a profile's approval button or reject the proposal below. No changes happen until you confirm."
        if action_token
        else "This saved copy cannot submit decisions. Run stockrank universe-review to open the latest proposal with approval controls."
    )
    nomination = ""
    if action_token:
        tickers = (
            considered if considered is not None else proposal["policy"].get("consider_tickers", [])
        )
        nomination = f'''<section><h2>Add stocks to consider</h2>
<p>Enter your candidate tickers, separated by commas (up to 25). They are saved for future searches and checked alongside the automatic candidates.
After the refreshed review opens, use <strong>Other eligible stocks you can include</strong> to select a passing candidate. Failed checks appear under Excluded candidates.</p>
<form method="post" action="/nominate"><input type="hidden" name="token" value="{e(action_token)}">
<label>Your candidate list <input name="tickers" value="{e(", ".join(tickers))}" maxlength="500" style="width:100%;padding:12px;font:inherit"></label>
<button>Save candidates &amp; refresh proposal</button><p>Replace this list to stop considering a ticker. This does not remove an active stock.
Add candidates before customizing a profile: refreshing resets unsaved checkbox selections.</p></form></section>'''
    approved_action = ""
    if action_token and decision and decision.startswith(("approved", "rejected")):
        approved_members = (
            f"<p><strong>Selected list ({len(receipt['members'])} stocks):</strong> "
            + e(", ".join(m["ticker"] for m in receipt["members"]))
            + "</p>"
            if receipt
            else ""
        )
        approved_action = f'''<form method="post" action="/report"><input type="hidden" name="token" value="{e(action_token)}">
<p>Your decision is saved. Build a report using the selected list to open the dashboard.</p>{approved_members}
<button>Build updated dashboard</button><p>The original proposal alternatives are preserved below for reference.</p></form>'''
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Universe proposal review</title>
<style>
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#f3f5f1;color:#20362c;
font:16px/1.6 system-ui,sans-serif}}main{{max-width:1250px;margin:auto;padding:45px 28px 80px}}
h1{{font-size:40px;line-height:1.15;margin:12px 0}}h2{{font-size:28px;margin:0}}p{{max-width:950px}}
.eyebrow{{font-size:12px;letter-spacing:2px;font-weight:700}}nav{{display:flex;gap:12px;margin:26px 0}}
nav a{{background:#284e3b;color:white;padding:10px 20px;border-radius:8px;text-decoration:none}}
section{{background:white;padding:28px;border:1px solid #dce4da;border-radius:14px;margin:24px 0;
scroll-margin:20px}}.stats{{display:flex;gap:45px;flex-wrap:wrap;margin:25px 0}}.stats b{{display:block;
font-size:30px}}.stats div{{color:#506858}}.ready,.blocked{{padding:14px;border-radius:8px}}
.ready{{background:#eaf3e9}}.blocked{{background:#fff1d7;color:#744a0b}}small{{display:block;color:#68736a}}
.table{{overflow-x:auto;margin:24px 0}}table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;vertical-align:top;padding:12px 10px;border-bottom:1px solid #e6ebe3}}
th{{background:#f5f7f2;white-space:nowrap}}td:nth-child(2){{min-width:170px}}td:last-child{{min-width:120px}}
summary{{cursor:pointer;font-weight:600}}details p{{max-width:350px}}.add,.keep{{padding:3px 8px;
border-radius:5px;font-size:12px;background:#dfefdb}}.keep{{background:#edf0ea;color:#5c675d}}
.sector{{display:grid;grid-template-columns:220px 1fr 30px;gap:15px;align-items:center;margin:8px 0}}
meter{{width:100%;height:16px}}a{{color:#286442}}code{{overflow-wrap:anywhere;font-size:13px}}
.note{{color:#5b695f}}@media(max-width:700px){{main{{padding:24px 12px}}section{{padding:18px}}
h1{{font-size:32px}}.stats{{gap:22px}}.sector{{grid-template-columns:1fr 1fr 25px;font-size:12px}}}}
.removals{{background:#fff5ef;border:1px solid #efcabc;padding:18px;margin:22px 0;border-radius:8px}}
.removals li{{margin:12px 0}}.remove{{background:#f7d9cf;color:#803820;padding:3px 8px;border-radius:5px;font-size:12px}}
button{{background:#284e3b;color:white;padding:12px 18px;border:0;border-radius:7px;font:inherit;cursor:pointer;margin:5px}}
button.secondary{{background:#eee9e3;color:#653b2b}}button:disabled{{opacity:.45;cursor:not-allowed}}
.actions{{border-top:1px solid #dce4da;padding-top:14px}}.actions p{{font-size:14px}}
</style></head><body><main><div class="eyebrow">STOCK RESEARCH ASSISTANT · UNIVERSE REVIEW</div>
<h1>Choose the market view you want.</h1><p>Compare two proposed universes from the same evidence.
{notice}</p>
{approved_action}{nomination}
<p class="note">{"On-demand preview · weekly schedule unchanged" if proposal["preview"] else "Weekly discovery proposal"}
<br>Created {e(proposal["created_at"])} · Market date {e(proposal["evidence"].get("market_date"))}
<br>{len(candidates)} candidates assessed · Model {e(proposal["model_version"])} · Active universe {e(proposal["base_universe"])}</p>
<nav><a href="#best_overall">Best overall</a><a href="#diversified">Diversified</a></nav>
<details><summary>How to interpret this proposal</summary><ul>{warnings}</ul></details>
{"".join(sections)}<section><h2>Excluded candidates</h2><p>Exclusion from this search does not establish that a company is a poor investment.</p>
<div class="table"><table><thead><tr><th>Ticker</th><th>Reason</th></tr></thead><tbody>{excluded}</tbody></table></div></section>
<p class="note">Proposal ID <code>{e(proposal["id"])}</code><br>JSON beside this page preserves the screening evidence and exact membership.</p>
</main></body></html>"""
