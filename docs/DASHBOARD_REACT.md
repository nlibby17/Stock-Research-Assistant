# Local React dashboard

Approved presentation migration, 2026-09-06. No scoring, providers, schema,
production universe, SEC promotion or weekly-discovery behavior changes.

Latest presentation follow-up (user approved for commit/push): VS score, Coverage, and key-metric
panels and Research score/chart panels reuse Market Overview's gold treatment.
VS Coverage is expandable and initially collapsed. Sector borders are permanently
highlighted without hover changes; Research info cards no longer highlight on hover.
Home navigation cards have a permanent subtle gold tint and aligned icon/title
positions. A shared crossed-swords SVG replaces the Stock Comparison navigation
text icon in Home and the sidebar. Verified with 14 frontend tests, rebuilt assets,
and browser checks of alignment, panels, and Coverage expansion.
Review correction: both navigation locations share complete crossed-sword geometry
with a clear blade overlap. Farewell motion is a further 40% faster than the
previously accepted 1.5-speed texture, with direction and exit behavior unchanged;
all 13 farewell tests pass. The user accepted these follow-up changes before authorizing commit/push.

Presentation follow-up, 2026-09-06 (user reviewed and approved for commit/push): Home
sector cards are compact rectangles with stationary hover highlighting. Navigation
card icons and text are larger; corner arrows retain their size. Market Overview
and the two Top Candidates panels share the Home title's gold border and background.
Candidate bars progress from deep gold on the left to pale gold on the right,
with the original gold at seventh position in a ten-stock chart. Research overview
metrics reuse Home's status-card treatment with a stationary hover glow, and the
rank/sector label is larger. VS and Advanced are unchanged.
The Research factor chart uses a shorter plotting area and wider bars, retaining
the 0–100 scale and existing tooltips. The shared page background adds faint gold,
violet, and blue radial washes over a softer grid. The user accepted these
refinements and the farewell visuals before authorizing this checkpoint.

The farewell mesh has denser detail and lateral parallax: upper rows move left,
lower rows move right. The center line, message, colors, sizing, and exit controls
retain their existing behavior. Verification: 14 frontend tests and 12 farewell
tests pass; production assets rebuilt; desktop and narrow Home layouts inspected.

## Run and fallback

`stockrank dashboard` and the existing morning/double-click flow use React.
`stockrank dashboard --ui streamlit` retains the existing dashboard implementation.
Only run `morning` when new calculations/provider work are wanted; `dashboard`
reads the latest stored report. Reload after a new report or research import.
The CSV endpoint refuses to export a different latest run than the displayed one.

Normal use requires Python only. The server binds to `127.0.0.1`, validates Host
and Origin, refuses cross-site reads, exposes only an explicit static-file list
and two GET endpoints, and reads SQLite inside one read-only transaction. No
telemetry, remote fonts/images, cloud host or UI write endpoints. Qualitative
Markdown rejects raw HTML; only HTTP(S) source links are enabled. Local-only
serving is not a security boundary against other processes on the same computer;
do not expose this server through a proxy or tunnel.

## Feature placement

| Existing content | React location |
| --- | --- |
| Hero, report/status badges, market table and sector leaders | Home |
| Candidate table, score tooltips, overall score chart, all-rankings CSV | Top Candidates |
| Compatible previous-report changes and comparison limitations | Top Candidates |
| Two-stock selection, component scores/coverage, metrics, research/sources | Stock Comparison |
| One company's overview, research and bounded SEC filings/sources | Research, numbered selector |
| Personalization commands and current profile/universe | Advanced, open initially |
| Run manifest, warnings, peer counts, freshness, provider health | Advanced disclosure |
| Cutoff-bound financial snapshots and report-linked provider comparison | Advanced disclosure |

Missing evidence remains unavailable, not zero or invented. Formula identities,
stored eligibility rules, legacy limitations, source dates and report/current
distinctions are retained. Chart data is available below each chart. Charts use
SVG rather than the old Altair-specific export/spec menu; the Streamlit fallback
still has that menu.

Navigation changes the in-memory view, not the URL or browser document; only Home
navigation returns to Home during the session. Reload starts at Home. Only the
selected research company is mounted. The number menu contains real eligible
report candidates (normally ten), never fabricated placeholders to fill ten slots.
Reduced-motion preferences disable entry animations and pointer tilt. Navigation
also expands on keyboard focus. The three-bar header is decorative, with no click
action or hover highlight. The sidebar fits its items rather than stretching to
the bottom of the window; short viewports allow scrolling to every navigation item.

Collapsed disclosures mount their contents only while open, including nested
diagnostics. Closing a section releases its rendered tables and JSON; reopening
starts its nested sections and table pages fresh. Shared data tables display at
most 25 rows per page, with Previous/Next controls and the full record count.
This avoids rendering all 500 stored comparison rows on entry to Advanced while
keeping every record accessible. The all-rankings CSV remains complete.

## Frontend development

From `frontend/`, with Node.js 24 and npm:

```text
npm ci
npm test
npm run format:check
npm run build
```

On PowerShell use `npm.cmd` if script execution policy blocks `npm.ps1`.
Source is in `frontend/src`; locked dependencies and the build script create
`src/stockrank/web_assets/`. Commit rebuilt assets with source after approval.
The bundle is included as Python package data; consumers never need npm.
CI verifies views and checks that rebuilding produces no bundle changes.

## Terminal farewell

While the dashboard runs, the welcome banner's small stock line draws left to
right in green (`#22C55E`), revealing one column every 0.5 seconds and looping.
Its axes and message stay stationary. Ctrl+C stops the dashboard and proceeds to
the existing farewell. Redirected output, CI, unsupported terminals, and small
windows use static welcome art; shrinking below 65 columns or eight rows stops
repainting to protect the terminal history.

Animation revision approved for commit/push, 2026-09-06: dense projected clusters of letters, numbers,
and symbols move right on the upper plane and left on the lower plane. Texture
projects through the horizontal center of the message line without static rails
or crossbars, and moves 50% faster than the initial dense revision.
The center line and exit controls are preserved. Focused tests cover density,
absence of static rails, reversed movement,
resizing, and exit behavior.

The upper/lower perspective-plane algorithm is independently authored, with no copied code,
frames or artwork from the referenced animation. `thank you goodbye` is stationary
at the horizon. A cobalt-blue to icy-white gradient colors both halves. Dimensions are read every frame, reserving rows for
`Close terminal? [y/n]`. Y + Enter exits; N + Enter continues indefinitely;
The typed Y/N is rendered in bright white beside the visible cursor. Ctrl+C is an
escape. Console modes and cursor visibility are restored on exit.
Animation uses an alternate screen so earlier report messages remain recoverable.

TTY/ANSI-capable Windows and POSIX terminals animate. Unsupported consoles use a
compact static horizon; redirected/CI output never waits for input. The launcher
no longer adds a second success pause. Y exits the launcher process; it cannot
force-close an unrelated parent shell. macOS may retain its finished window based
on existing terminal preferences. Closing a window with its X button cannot reliably
play a farewell after the window is gone; use Ctrl+C to stop the dashboard first.

On Windows, the compatibility `.cmd` wrapper starts `stockrank.desktop_launcher`
in its own Python console and immediately exits. There is no waiting batch
interpreter to issue an additional `Terminate batch job (Y/N)?` after Ctrl+C.
The Python console runs the same `morning` command and pauses only on failures.
Relaunch through the desktop shortcut to use this fix; a previously open launcher
still has its old process structure.

Preview without generating a report: `python -m stockrank.farewell`.

## Verification / acceptance

- Python tests cover read-only/no-run behavior, missing values, legacy cutoffs,
  snapshot build time and membership, shadow linkage, local-only HTTP access,
  file allowlisting, CSV consistency and private error text.
- Terminal tests cover frame dimensions, static text, resize, N continuation,
  Y confirmation, cleanup and noninteractive fallback. Windows pseudo-terminal
  execution also confirmed animation and N/Y handling.
- Frontend tests compile/render actual views with isolated fixtures; no fixtures
  are shipped as dashboard data. Tests cover formatting, distinct pair selection,
  missing values, escaped content, default research selection and vertical bars.
- Browser checks use existing stored reports, not new daily runs. User visual
  acceptance is complete. On 2026-09-06 the user also reported flawless operation
  on their Mac after updating. This is user-reported manual verification, not an
  agent-run test or a claim about every macOS version. Streamlit remains optional.
- Hosted [CI run 34012607584](https://github.com/nlibby17/Stock-Research-Assistant/actions/runs/34012607584)
  completed successfully for `9acd197`; the workflow covers Python on Windows,
  macOS and Ubuntu, plus frontend tests, formatting and bundle consistency.

Local checkpoint after finishing touches: 345 Python tests passed, four platform-specific
tests skipped; 11 frontend tests and production build passed. Browser checks confirmed all five
views, distinct pair selection, numbered research/filings, current diagnostics,
square cards at a narrow viewport, and chart replay after leaving/re-entering view.
An isolated wheel build confirmed all five frontend assets/notices are packaged.

Finishing review: Home orders navigation cards, sector leaders, market table,
gold-accented status cards, then Advanced. Top Candidates starts with its graph;
the `Candidate Score Comparison` heading now names the table below it. The redundant
eligibility banner is removed, not the eligibility rules. Graph details use explicit
pointer/focus tooltips. Research has larger title-case tier badges and reactive
number cards; Stock Comparison adds tilt only. A Windows pseudo-terminal test
confirmed the typed Y is visible and one Enter exits the farewell.

User visually accepted the dashboard and explicitly authorized this checkpoint's
commit and push on 2026-09-06. It is committed and pushed as `9acd197`
(`Migrate dashboard to React and refine animated launch experience`). The later
Mac confirmation closes the remaining manual platform check for this checkpoint.
No production input change is included. See `ROADMAP.md` for the next work item;
this migration does not authorize future feature implementation.
