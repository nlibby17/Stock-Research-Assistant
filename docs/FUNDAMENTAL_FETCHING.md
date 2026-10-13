# Bounded fundamentals fetching

The report pipeline refreshes missing or expired Yahoo fundamental summaries with
at most three concurrent provider calls. This applies to `run`, `daily-report` and
the report built by `morning`, including the post-universe-review report. Universe
discovery's screening loop remains serial; this work starts with one caller.

`fundamentals.refresh_fundamentals` owns cache selection, fallback, ordered result
merging and progress. Worker threads call only the provider. All application SQLite
reads and writes stay on the calling thread. Yahoo's separate internal cookie cache
is still managed by its client library. The production pool is enabled only when
the pinned Yahoo client reports its curl backend; disabled, unavailable or unknown
backends use the serial path, as do other providers.

Only cache misses are scheduled. Cache entries selected for reuse are checked again
before consumption in case earlier network waits crossed their expiration. The
existing maximum-age limit, stale fallback/rejection, provider retries, backoff,
warnings, source timestamps, scoring, coverage and JSON structure remain in place.
`--force` still refreshes the whole universe. Results and warnings are merged in
universe order, regardless of network completion order.

Terminal progress shows the cached count, refresh count and each checked ticker's
result (`refreshed`, `stale_fallback`, `rejected_stale` or `unavailable`). Progress is
emitted by the calling thread. A completely warm cache performs no network calls.

The scheduler submits a rolling window of at most three ordinary tasks. A cache
entry that expires after selection can add one queued task in the same pool; it
never adds a fourth active network call. Cancellation or a database/progress error
stops scheduling, cancels unstarted tasks and waits for running calls to finish
without merging their results. Python cannot safely kill an active HTTP request:
Ctrl+C may wait for those calls and their existing retry/timeout behavior to end.
No worker remains running after the refresh function exits.

The serial path is retained as `refresh_fundamentals(..., workers=1)` and as the
pipeline's internal `FUNDAMENTAL_FETCH_WORKERS` limit. No new user configuration,
dependency, scoring-model version or universe change is introduced. The extracted
module is included in the existing implementation fingerprint; historical runs
remain immutable and the existing comparison-compatibility rules still apply.

## Measurements — 2026-10-13

Before changing scheduling, isolated synthetic ranking/report builds took 2.88s
cold / 0.58s warm for 50 stocks and 5.27s cold / 0.95s warm for 100 stocks (one
baseline observation each). These are not eight-step daily-workflow measurements.

The repeatable comparison below uses three observations per mode, identical
deterministic fixture data and a 20ms simulated network wait per company. It times
the complete ranking/report build, including its SQLite and report-file work.
Every stored ranking row matched exactly across serial/concurrent and cold/warm
builds after excluding the unique run identifier.

| Universe | Cache | Serial median | Three-worker median |
|---|---|---|---|
| 50 stocks | Cold | 2.9694s | 1.7711s |
| 50 stocks | Warm | 0.6901s | 0.7065s |
| 100 stocks | Cold | 5.3251s | 3.1664s |
| 100 stocks | Warm | 1.2524s | 1.2340s |

Cold fixture builds improved about 40%; warm caches do not benefit from network
concurrency. The extra freshness recheck adds cache-read overhead compared with
the original loop, and timings also vary with machine load. No end-to-end live
daily-report speedup is claimed.

An isolated live sample of the first 12 configured tickers took 2.7000s with one
worker and 1.3258s with three workers. Both returned 12/12 summaries, zero warnings
and identical values apart from fetch timestamps. Warm-cache checks took 0.0396s
and 0.0397s respectively. An earlier serial sample took 2.7950s with 12/12 available
and no warnings. These small, sequentially observed samples support the bounded
default; they do not establish Yahoo's future latency or throttling rate.

Live benchmarking writes only to an isolated database and local measurement JSON;
it never creates or replaces a production report. Raw measurements are private
under `runtime/`. Synthetic fixtures never replace live application data.

## Reproducing and verifying

Run from the repository root with the project environment's Python:

```text
python scripts/benchmark_fundamentals.py --output runtime/tmp/fundamental-fixtures.json
python -m pytest tests/test_fundamentals.py tests/test_pipeline.py tests/test_yfinance_provider.py
python -m mypy
```

Live requests require the explicit `--live` option. Use separate processes to avoid
sharing in-memory Yahoo response caches between modes; its local cookie cache is
kept in `runtime/cache/fundamental-benchmark`:

```text
python scripts/benchmark_fundamentals.py --live --workers 1 --output runtime/tmp/fundamental-live-one.json
python scripts/benchmark_fundamentals.py --live --workers 3 --output runtime/tmp/fundamental-live-three.json
```

The live sample is capped at 12 tickers. Tests independently cover 50/100-stock
equivalence, overlapping requests, deterministic merging, owner-thread storage
and progress, warm cache and force behavior, mid-run expiry, partial failure,
HTTP 429 retry exhaustion/recovery, cancellation and database failures. Timing
thresholds are deliberately excluded from CI assertions.
