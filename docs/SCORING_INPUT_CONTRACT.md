# Scoring input contract

`stockrank.scoring_inputs.ScoringInput` describes the normalized dictionary passed
to `score_universe` and `metric_peer_counts`. Both the daily report pipeline and
universe discovery construct `dict[str, ScoringInput]`, keyed by ticker.

| Field | Required? | Value |
|---|---|---|
| `metrics` | Yes | Dictionary of metric names to `float` or `None`; integer numeric values are also accepted by Python typing. |
| `company`, `sector` | No | String or `None`; absent, empty or `None` values retain the configured security fallback. |
| `price_as_of` | No | ISO date string or `None`; absence remains unavailable. |
| `warnings` | No | List of strings; absence defaults to an empty list. `None` is not supported. |

Metric names remain configuration-driven. Missing keys and explicit `None` values
still mean unavailable data. The contract does not add metrics, assign missing-value
penalties, change weights, or change score/coverage/eligibility calculations.

Discovery retains this type in `DiscoveryEvidence.verified_inputs` through copying,
incremental candidate additions and rescoring. Its candidate metadata has a separate
`DiscoveryRow` contract so broad provider documents do not erase the type of scoring
inputs. Existing dynamic listing, SEC and fundamental payloads remain outside this
bounded contract.

These are ordinary dictionaries at runtime, with the same serialization. Static
types do not validate external JSON, date formats, finite numbers, freshness or
financial meaning. Existing provider validation and runtime quality gates remain
necessary and unchanged.

## Development checks

Run from the repository root using the project's virtual-environment Python
(`.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on macOS/Linux):

```text
python -m mypy
python -m pytest tests/test_scoring_input_types.py
```

The default mypy targets are `scoring_inputs.py`, `scoring.py`, `pipeline.py` and
`universe_sources.py`. Imported project signatures are analyzed with
`follow_imports = "silent"`; unrelated modules are not new diagnostic targets and
are not replaced with unchecked imports. This is not a repository-wide typing
campaign. External provider payloads keep their existing dynamic interfaces.

Mypy and Requests stubs are managed development dependencies in both hashed lock
profiles. Use the existing [dependency workflow](../requirements/README.md) when
installing or changing them. CI runs the scoped check on every supported Python/OS
combination and the macOS compatibility profile. Contract tests prove that minimal
and nullable inputs pass, while missing/renamed required keys, incompatible values
and malformed inputs to scoring, peer counts or retained discovery evidence fail.

Runtime regression coverage remains in the scoring, pipeline and universe-discovery
tests, including missing data, ties, freshness, fallback and evidence reuse.
