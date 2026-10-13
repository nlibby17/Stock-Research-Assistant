# CLI command ownership

Step 6 of the reliability plan separates command orchestration by responsibility.
The public interface remains `stockrank <command>`; the Python and desktop entry
points still call `stockrank.cli.main`.
The extraction reduced the entry point from 1,871 lines to 59; eight command
modules keep related commands and their helpers together.

## Module map

All paths below are relative to `src/stockrank/`.

| Module | Responsibility |
|---|---|
| `cli.py` | Connect command names to handlers, parse arguments, dispatch once, and report top-level errors. |
| `command_parser.py` | Define command names, flags, defaults, help, and argument validation. Existing universe parser registration stays here. |
| `configuration_commands.py` | `setup-check`, `config-check`, and `configure`, including interactive prompts and bundled React asset checks. |
| `report_commands.py` | `run`, `validate-latest`, and `research-import`; present analysis results and validate the exact stored report. |
| `storage_commands.py` | `storage-status` and `storage-clean`; present inventory and coordinate preview/apply with the existing cleanup policy. |
| `sec_filings_commands.py` | `sec-health`, `sec-filings-sync`, and `sec-filings-status`; identity and filing acquisition/coverage. |
| `sec_facts_commands.py` | `sec-facts-sync` and `sec-facts-status`; Company Facts refresh decisions, acquisition, and coverage. |
| `sec_financial_commands.py` | `sec-financials-build` and `sec-financials-status`; invoke the existing local calculator and store/present its snapshots. |
| `provider_commands.py` | `provider-shadow-run` and `provider-shadow-status`; isolated comparison and promotion-evidence reporting. |
| `workflow_commands.py` | Assemble `daily-report`, coordinate `morning` with universe review, and launch the React `dashboard`. |
| `daily_workflow.py` | Existing step execution/timing and dashboard process, browser, welcome, and farewell lifecycle. |
| `universe_commands.py` | Existing universe configuration, proposals, review, approval, rejection, and parser registration. |

The command modules load settings, invoke existing application services, report
progress/results, and choose exit codes. Financial calculations remain in the
tested calculation modules, persistence remains in `Storage`, provider access
remains in the adapters, and retention policy remains in `runtime_maintenance.py`.

## Dependency direction

`cli` wires the parser to command modules. `workflow_commands` composes individual
handlers and uses `daily_workflow` to execute them. Command modules depend on the
existing application services; they never import `cli`.

Two small existing dependencies are explicit: live configuration validation calls
the SEC health handler, and Company Facts commands reuse the filing commands'
history-date and document-count helpers. These helpers keep their existing
behavior; no new utility framework or duplicate implementation is introduced.

The universe review worker imports `command_daily_report` from
`workflow_commands`, so its report build uses the same eight steps as the terminal.
Its local import stays at the report branch, preserving the worker's existing
initialization order. Python handler locations are internal implementation details;
tests patch the module that actually owns or invokes the relevant dependency.

## Behavior preserved

- The CLI split preserved all 26 command names, flags, defaults, help text, and
  exit-code behavior. The separately approved October 13 Streamlit retirement
  subsequently removed the dashboard `--ui` selector.
- `daily-report` retains the same eight steps and force propagation. Invalid
  configuration stops provider access; a failed ranking skips shadow comparison.
  Standalone `run` still validates once, and the daily workflow still performs its
  one final validation.
- `morning` still checks discovery first. Approval/rejection, report retries,
  visible auxiliary SEC warnings, and the single-dashboard handoff are unchanged.
- React is now the only dashboard; launcher entry points and the terminal
  lifecycle are unchanged.
- The split does not change scoring, formula identities, schema, provider policy,
  stored history, or personal configuration.

For an interview walkthrough, start at `cli.build_parser`, follow `daily-report`
into `workflow_commands`, and then follow its ranking step into `report_commands`
and the existing pipeline. This shows command routing, workflow composition, and
application calculations as distinct responsibilities without a command framework.

## Verification

The CLI contract tests exercise every command through `main` with isolated
handlers, including exit-status propagation, help, parser errors, and handled
application failures. SEC command characterization tests cover empty history,
invalid inputs, unavailable providers, and persisted failure health. Existing
tests continue to cover configuration, reporting, cleanup safety, financial
snapshots, evidence qualification, launchers, and universe review/report handoffs.

The extraction also compares the pre/post command surface and handler syntax trees
to detect unintended edits. Full-suite results and release status are recorded in
[the roadmap](ROADMAP.md). The CLI refactor and subsequent React-only cleanup are
released. Further structural work is deferred under the current accepted scope.
