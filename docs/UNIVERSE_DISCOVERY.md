# Universe discovery and local testing

This feature has passed user testing and is approved for release. It creates reviewable proposals; it never automatically changes membership.
No broker, background task, paid provider, or AI service is involved.

## Try it now

From the project directory on Windows:

```powershell
.\.venv\Scripts\stockrank.exe universe-preview --open
```

On macOS, use `./.venv/bin/stockrank universe-preview --open`.

This fetches current public data and writes a unique JSON proposal and a readable
HTML page under `runtime/universe/`. Both profiles use exactly the same evidence.
The page has company scores, coverage, price dates, sector counts, additions,
removals with reasons, exclusions, and expandable warnings/SEC links. Removals are
shown in a visible section near the top of each profile, above the Add/Keep table.
With `--open`, the command starts a local review server and opens the interactive
page with **Review & approve** and **Reject this proposal (both profiles)** buttons.
You can customize a profile before approval; rejection closes both alternatives.
Each action first opens a confirmation page. No membership changes happen until
the final confirmation. Keep the terminal open while reviewing; Ctrl+C stops it.
The standalone saved HTML file remains read-only and explains how to reopen the
interactive review.

Reopen your latest saved proposal without fetching data again:

```powershell
.\.venv\Scripts\stockrank.exe universe-review
```

Use `--file runtime/universe/proposal-<id>.json` to select an earlier proposal.
The server binds to a randomly assigned loopback port and serves only that proposal.
It validates Host/Origin and a random review token; final decisions additionally
require a single-use, ten-minute confirmation. It reuses the existing activation
checks and lock and exposes no arbitrary file-serving route. Opening a saved review
does not refresh data; submitting the candidate form explicitly starts a new search.

## Customize your list

- Check **Keep this stock now and in future reviews** beside a proposed removal.
  Current retained stocks also have a **Keep in future reviews** checkbox. These
  protections persist after approval. Missing data and exclusion warnings remain
  visible; keeping a stock does not make it pass the screening checks.
- Check **Remove from this selection** to drop a proposed addition or retained
  member. Uncheck its keep option first if it is protected. This edits the pending
  list; nothing changes until final confirmation.
- Use **Add stocks to consider** before editing a profile. Enter up to 25 tickers,
  separated by commas, and refresh. These candidates are saved for future searches
  and go through the same checks. Passing candidates can be selected under
  **Other eligible stocks you can include** even if the automatic profile did not
  choose them. Failed candidates remain in the exclusion list. Refreshing resets
  unsaved checkbox edits. Removing a candidate preference does not remove an active
  member.
- Overrides can grow the list beyond its usual target, up to a **hard cap of 100**.
  If the selection exceeds 100, the review shows the exact excess and offers manual
  removals or **Suggest a trim to 100**. A suggested trim preserves protected stocks
  and prefers lower scores; Diversified first trims overrepresented sectors. Both
  paths show the final list for confirmation. No suggested trim activates itself.
  At least ten members are required. Explicit edits that lose diversified sector
  coverage are called out on the confirmation page.

After **Confirm & build updated dashboard**, approval is saved, a progress page
builds and validates a fresh deterministic daily report, and the browser opens the
React dashboard using that exact approved membership. This does not perform the
separate AI research phase. If the report fails, approval remains saved and the
page offers a retry with progress details; it does not open an old report as new.
Incomplete SEC Company Facts, SEC snapshots, or the isolated SEC/Yahoo comparison
do not block a newly completed ranking report when configuration, ranking, final
validation, and exact approved membership all pass. These issues remain visible in
a warning at the top of the dashboard, saved against that report's run ID. Other
failed or unknown checks still block the handoff. The normal `daily-report` command
continues returning an attention-required status for any failed step.

Build output is streamed live to the launching terminal and the progress page.
After the dashboard opens, the terminal uses the existing animated ASCII welcome;
Ctrl+C uses the existing farewell. No second dashboard command is needed.
An already-approved review shows its recorded membership and **Build updated
dashboard**, without requiring approval again. Keep the terminal open through the
report build and while using the dashboard.

Older proposal files remain readable, but pending proposals created before the
override update require a new preview before activation. Existing approvals remain
valid and can use the dashboard-build button.

Preview does not alter the active universe, scoring configuration, report database,
or last successful weekly discovery. Rerun immediately as often as needed; there is
no week-long wait. It uses fresh listing/screener/market requests and the SEC
client's short-lived submission cache. No stale-on-error SEC fallback is allowed.

## Profiles and bounded scope

- **Best overall:** highest available scores in the screened pool under the current
  scoring settings, without sector quotas. It can concentrate in a few sectors.
- **Diversified:** repeatedly selects the highest-scoring company from the
  least-represented available sector. All 11 sectors must be present. With enough
  eligible companies in every sector, a 50-stock proposal has four or five in each.
  If a sector has fewer eligible companies, remaining slots go to other sectors;
  the exact counts stay visible. This balances company counts, not investment
  weights, geography, company size, or portfolio risk.

Both profiles select at most one share class per SEC issuer. An eligible active
share class takes precedence over another class of the same company in the scoring
pool. Ties use ticker order. "Best overall" is relative to the screened pool, not a
claim to have found the best stocks in the market or a prediction of returns.

The initial search takes up to ten companies per Yahoo sector, ordered by market
capitalization, plus every active member and up to 25 user-nominated tickers.
It examines at most 110 automatic new candidates
before deduplication, rather than downloading fundamentals for the entire market.
This is intentionally biased toward larger companies. The sector search uses a
$5 quote-price floor. The remaining checks use completed daily prices.

Eligibility requires current Nasdaq Trader listing identity on Nasdaq, NYSE, or
NYSE American; an explicit common/ordinary-share name classification; a unique SEC
ticker/CIK; recent verified 10-K reporting; 300 completed trading sessions; recent
continuous prices aligned with the market reference; a $5 minimum price; $10m
median daily dollar volume across all 20 latest sessions; and at least 60% score
coverage (or the user's higher coverage threshold). Shared Python metrics, sector
conventions, scoring weights, freshness, and minimum-peer rules are reused. The
top-candidate score threshold is not a universe-membership threshold.

ETFs, funds, preferreds, units, warrants, depositary securities, blank-check SEC
classifications, unresolved listing/security types, and foreign/transitional filing
contracts are excluded. A familiar company can be excluded when its directory
name does not prove the supported share type. This is a scope limit, not a negative
investment judgment. Foreign incorporation alone is not the gate: verified domestic
10-K reporting is the supported accounting contract. Historical SEC submission
shards are checked when a high-volume filer's recent list lacks its annual report.

Nasdaq financial-status warnings and absent/changed identities are explicit
exclusions. Merger and deregistration filing forms are **review warnings**, not
automatic evidence that the common stock is being delisted: a filing can concern
debt or an acquisition. SEC links and checked filing metadata are retained. The
initial workflow does not claim comprehensive pending-merger/delisting detection or
resolve identity overrides. Review unresolved corporate events before approval.

There is no hidden score bonus for incumbents or multi-week persistence gate. This
is a single dated proposal and may suggest substantial turnover. Approval is the
change-control gate. Scores are relative to this pool; a subsequent daily report
recalculates percentiles against the newly approved membership.

## Change the proposal settings

These commands select a preferred weekly profile and search limits. They never
change the active universe or your growth/value/etc. scoring profile:

```text
stockrank universe-configure --profile best_overall
stockrank universe-configure --profile diversified --size 50 --per-sector 10
stockrank universe-status
```

Use the platform-specific executable prefix shown above. Size accepts 11–100;
candidates per sector accepts 5–25. Every preview still includes both profiles.
The preferred profile controls whether a weekly result counts as successful.
Approving the other profile does not silently change this saved preference.

Settings are saved to ignored `config/universe-discovery.local.json`. They are
separate from `stockrank configure`, which still manages scoring/manual membership.
`universe-configure --disable` pauses weekly discovery; `--enable` restores it.
On-demand preview remains available while disabled. Defaults enable weekly
proposals with the Diversified profile and a 50-stock target.

## Approval and rejection

The interactive review supports both decisions directly. Terminal commands also
remain available; use the exact proposal JSON path printed by preview:

```text
stockrank universe-reject --file runtime/universe/proposal-<id>.json
stockrank universe-approve --file runtime/universe/proposal-<id>.json --profile diversified
```

Approval prints exact membership/diffs and asks for explicit confirmation. `--yes`
means the caller has already reviewed and approved that exact proposal/profile;
it is not an option to use for casual testing on the real installation.

Approval rejects blocked, modified, already-decided, older-than-48-hour, and
configuration-incompatible proposals. It rechecks selection against saved evidence,
creates a versioned ignored `config/universe.discovery-<id>.local.csv`, and updates
only the personal preferences' universe table. Other settings and comments remain.
Membership, preferences, persistent keep choices, and the approval receipt use the existing rollback
transaction with backups. Failed writes restore the previous configuration or
report recovery requirements. Historical runs are not rewritten. Browser approval
then creates a new daily report; the terminal-only approval command just activates
membership and leaves report generation to the next daily run.

Browser rejection records the unchanged stock list and starts its report build,
then opens the verified dashboard. The CLI rejection command only records the
decision. Neither changes active membership or the normal weekly schedule. After either decision, generate a new preview to reconsider
membership. `universe-status` lists recent proposals and their decisions.

## Weekly behavior

`morning` checks discovery before any of the eight report steps and opens the
interactive review when a proposal is available. Confirmation of either approval
or rejection starts the report exactly once for the chosen list.
When discovery is not due (or cannot produce a proposal), it opens the completed
report dashboard normally. Validated rankings with incomplete SEC facts/snapshots/
shadow comparison can proceed with visible warnings, using the same narrow gates
as the post-approval build. Core or unknown failures still stop the workflow.
The user can test this with `stockrank morning`; use `universe-status` to check
whether discovery is due first. No clock override is necessary when it is already due.

At the start of `morning`, discovery runs if there has been no successful
discovery for the effective universe/scoring/discovery/SEC/provider context in the
last seven elapsed days. Repeating a daily run does not repeat weekly work.
Approving a weekly proposal carries its original successful discovery timestamp
forward to the approved membership and protected-stock choices. It does not trigger
another discovery on the next launch. Existing approval receipts also retain this
cadence. Other changes to that context make it due again. `daily-report` alone does not discover;
`morning --force` only bypasses the report's provider caches, not this weekly gate.

After approval, the review server monitors the report worker independently of the
browser. Once the approved membership and completed report are verified, it opens
the dashboard and starts the terminal graph, even if the progress tab was closed.
The existing review tab gets three seconds to navigate first. If it does not,
the launcher opens one dashboard tab; a delayed progress refresh then shows a link
instead of automatically opening a second dashboard.
Failures appear in the terminal with a progress/retry link. `--no-open` continues
to suppress automatic browser opening.

Success means the preferred profile has enough eligible members and no retrieval
errors or profile blockers. Failed/blocked discovery does not advance the schedule;
the next morning run retries. Its diagnostics do not invalidate a
completed daily report or prevent the dashboard from opening. A single local lock
prevents overlapping discovery/decision operations. After a process crash, verify
the process has stopped before removing the printed lock path. There is no daemon.

## Development verification and user acceptance

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_universe_discovery.py -v
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

The first command uses synthetic fixtures in temporary directories, never live
membership. It exercises the first run, same-day/six-day/seven-day timing,
failure/retry, context changes, both selectors, sector shortages, duplicate issuers,
listing/filing restrictions, unchanged preview/rejection state, exact prospective
activation, preserved history/configuration, and rollback after a failed write.
Existing metric/freshness/scoring tests remain part of the full suite.

Earlier verification on 2026-09-09: 394 Python tests passed and four platform-specific
tests skipped; the discovery suite contains 35 passing tests. Ruff and Git diff
checks passed. The final live preview assessed 121 candidates, with 86 eligible;
both profiles produced 50 members without provider blockers. Best overall proposed
20 swaps and Diversified 23, with four/five companies in each diversified sector.
Proposal integrity/selection reconstruction passed, the active universe remained
`us_diversified_50_v1`, and no weekly success timestamp was created. Earlier
development previews are retained separately under `runtime/universe/development-previews/`.
The initial static-file browser inspection was blocked by local-file URL policy.
The subsequent interactive review was inspected through its local server: visible
removals, approval confirmation, and cancellation were verified without submitting
a real decision. HTTP tests exercise approval/rejection in temporary configurations,
invalid origins/tokens, replay prevention, and unserved file paths. User acceptance
was pending at that checkpoint; final user acceptance is now complete. The follow-up suite passed 397 tests with four skips, including 38
discovery/review tests; Ruff passed. No new Mac manual test, hosted CI run, commit,
or push is claimed.

The user subsequently approved a proposal during their manual review. The old
page incorrectly continued showing an approval-required message and did not build
the new report. Those were workflow bugs, not a test-mode restriction. The updated
flow separates saved approval from report-building status.

Override/handoff verification: 406 tests passed, four platform-specific tests
skipped, and Ruff passed. The 47 discovery/review tests cover persistent protected
members, screened nominations, manual removals, cap enforcement, suggested trims,
report failures, and the dashboard's actual pipeline data contract. An isolated
browser check confirmed that keeping a proposed removal grows the final count from
50 to 51. No real membership changes were made by these tests; live acceptance of
the revised flow remains with the user.

Follow-up verification for the GEV missing-start-date case: 411 Python tests and
14 frontend tests passed; frontend assets were rebuilt. A live retry preserved the
approved 50-stock universe, validated its new ranking run, and completed the handoff
with explicit SEC warnings. Browser inspection confirmed the warning above the
working dashboard. Terminal output relay and reuse of the existing welcome/farewell
functions are regression-tested. The SEC source-data issue itself remains flagged;
no dates, facts, or scores are invented or substituted.

For user acceptance, open a fresh preview, try keeping a proposed removal, add a
candidate, check the confirmed stock total and removals, and confirm only the exact
list you want active. Compare sector balance and proposed
turnover, inspect removal reasons and warnings, and try another size or search
limit with an immediate preview. Accepting the feature and approving a membership
proposal are separate decisions. No commit or push should happen until the user
has finished this review and explicitly authorized the release.

Source contracts: [Yahoo/yfinance screener API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.screen.html),
[Nasdaq symbol directory fields](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs),
and the project's existing SEC client. Runtime proposals and personal files remain
ignored by Git and are not release artifacts.
