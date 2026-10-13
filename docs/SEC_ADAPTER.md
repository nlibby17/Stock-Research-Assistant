# SEC adapter ownership

The October 13, 2026 maintenance split separates acquisition, configuration and
normalization without changing the SEC data contract or financial calculations.
Commands still import from `stockrank.data.sec`.

## Module map

All paths below are relative to `src/stockrank/data/`.

| Module | Responsibility |
|---|---|
| `sec.py` | Public imports used by commands, calculations and existing tests. |
| `sec_types.py` | Immutable response records, concept specifications and provider errors. |
| `sec_transport.py` | Contact-identity validation, permitted hosts, HTTP requests, throttling, retries, compression and disk-cache handling. |
| `sec_configuration.py` | Settings validation, ticker normalization, entity overrides and concept-map loading. |
| `sec_filings.py` | Issuer identity directory, submission history, accession links and filing availability dates. |
| `sec_companyfacts.py` | Configured Company Facts normalization, filing lineage and effective-fact selection. |

The largest implementation module is 427 lines. This follows existing
responsibilities instead of dividing the original 1,269-line file at an arbitrary
midpoint. The 75-line public entry point re-exports the same objects; it introduces
no wrappers, provider framework or duplicated logic.

## Data flow and boundaries

Configuration defines the permitted requests and concept mappings. `SecClient`
retrieves or reuses SEC JSON documents. The identity and filing adapters interpret
those documents, and Company Facts associates normalized values with filing
evidence and availability dates. Existing commands store the resulting records.
Financial metric calculations remain in `sec_financials.py`.

Implementation modules import their dependencies directly, never through the
public `sec.py` entry point. Shared records are independent of the adapters.
Company Facts reuses filing accession/date parsing; it does not duplicate those
rules or trigger a new filing request during normalization.

Preserved behavior includes cache formats and expiration, request spacing,
retries and stale-data handling, entity overrides, concept priority, units,
amendments, source lineage and point-in-time selection. The complete formula
manifest, including its implementation fingerprint, matched the pre-split value.
Stored history needs no migration or formula-version change.

## Verification and walkthrough

The existing SEC tests exercise requests and cache failures, identity matching,
submission history, Company Facts normalization, effective selection and formula
identity. Public import checks confirm that callers share the implementation's
classes and errors. The full Python suite also exercises command orchestration,
storage, financial calculations and provider comparisons.

For a walkthrough, follow `sec-filings-sync` from `sec_filings_commands.py` through
`SecClient`, `SecIdentityDirectory` and `SecSubmissions`. Then follow
`sec-facts-sync` through `SecCompanyFacts`. This explains how source documents
become traceable records before any financial metric is calculated.
