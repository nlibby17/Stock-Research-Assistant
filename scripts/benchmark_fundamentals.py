"""Isolated step 5 measurements. Synthetic fixtures by default; --live opts into Yahoo."""

import argparse
import copy
import json
import logging
import statistics
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stockrank.config import Settings, load_settings
from stockrank.data.demo import DemoProvider
from stockrank.data.yfinance_provider import YFinanceProvider
from stockrank.fundamentals import refresh_fundamentals
from stockrank.models import Security
from stockrank.pipeline import run_analysis
from stockrank.storage import Storage

ROOT = Path(__file__).resolve().parents[1]


class FixtureProvider(YFinanceProvider):
    name = "step5-synthetic-fixture"
    freshness_label = "SYNTHETIC benchmark fixture; not investment data"

    def __init__(self, now, delay):
        super().__init__()
        self.now, self.delay = now, delay

    def supports_concurrent_fundamentals(self):
        return True

    def fetch_prices(self, securities, start, end):
        rows, warnings = DemoProvider().fetch_prices(securities, start, end)
        return {
            ticker: [replace(bar, source=self.name, fetched_at=self.now) for bar in bars]
            for ticker, bars in rows.items()
        }, warnings

    def fetch_fundamental(self, security):
        time.sleep(self.delay)
        snapshot, warnings = DemoProvider().fetch_fundamental(security)
        return replace(snapshot, source=self.name, fetched_at=self.now), warnings


def fixture_measurements(loaded, temporary, repeats, delay):
    """Compare complete ranking/report builds, including owner-thread SQLite work."""
    now = datetime.now(UTC)
    measurements = []
    for size in (50, 100):
        expected = None
        for workers in (1, 3):
            times = {"cold": [], "warm": []}
            for repeat in range(repeats):
                raw = copy.deepcopy(loaded.raw)
                raw["app"]["runtime_dir"] = "runtime"
                settings = Settings(
                    root=temporary / f"{size}-{workers}-{repeat}",
                    raw=raw,
                    universe=tuple(
                        Security(f"T{i:03}", f"Fixture {i}", "Industrials") for i in range(size)
                    ),
                )
                with (
                    patch(
                        "stockrank.pipeline.provider_for", return_value=FixtureProvider(now, delay)
                    ),
                    patch("stockrank.pipeline.FUNDAMENTAL_FETCH_WORKERS", workers),
                    patch(
                        "stockrank.pipeline.configure_logging",
                        return_value=logging.getLogger("benchmark"),
                    ),
                    patch("builtins.print"),
                ):
                    for state in ("cold", "warm"):
                        start = time.perf_counter()
                        run_id, _, warnings = run_analysis(settings)
                        times[state].append(time.perf_counter() - start)
                        rows = Storage(settings.database_path).get_results(run_id)
                        normalized = [
                            {k: v for k, v in row.items() if k != "run_id"} for row in rows
                        ]
                        if expected is None:
                            expected = normalized
                        if normalized != expected:
                            raise AssertionError(
                                "Serial/concurrent or cold/warm ranking results differ"
                            )
                        if any("refresh failed" in warning for warning in warnings):
                            raise AssertionError("Fixture provider refresh failed")
            for state, values in times.items():
                measurements.append(
                    {
                        "size": size,
                        "workers": workers,
                        "cache": state,
                        "median_seconds": round(statistics.median(values), 4),
                        "samples_seconds": [round(value, 4) for value in values],
                    }
                )
    return {
        "kind": "synthetic ranking/report builds (not the eight-step daily workflow)",
        "delay_per_fundamental_seconds": delay,
        "identical_results": True,
        "measurements": measurements,
    }


def live_measurements(loaded, temporary, workers, sample):
    raw = copy.deepcopy(loaded.raw)
    raw["app"]["runtime_dir"] = "runtime"
    settings = Settings(root=temporary, raw=raw, universe=loaded.universe[:sample])
    storage = Storage(settings.database_path)
    storage.initialize()
    provider = YFinanceProvider(
        retries=int(raw["provider"]["request_retries"]),
        backoff_seconds=float(raw["provider"]["retry_backoff_seconds"]),
    )
    provider._module().set_tz_cache_location(
        str(ROOT / "runtime" / "cache" / "fundamental-benchmark")
    )
    if workers > 1 and not provider.supports_concurrent_fundamentals():
        raise ValueError("Concurrent live measurement requires the supported curl backend")
    measurements = []
    for state in ("cold", "warm"):
        start = time.perf_counter()
        result = refresh_fundamentals(
            settings,
            storage,
            provider,
            now=datetime.now(UTC),
            workers=workers,
        )
        measurements.append(
            {
                "cache": state,
                "seconds": round(time.perf_counter() - start, 4),
                "available": len(result.snapshots),
                "warnings": result.warnings,
                "statuses": {ticker: value["status"] for ticker, value in result.lineage.items()},
                "snapshots": {
                    ticker: value.to_dict() for ticker, value in result.snapshots.items()
                },
            }
        )
    return {
        "kind": "live isolated fundamentals only",
        "workers": workers,
        "sample_size": len(settings.universe),
        "measurements": measurements,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch a bounded live sample; no production report writes.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        choices=(1, 3),
        default=1,
        help="Live mode only; fixtures compare both.",
    )
    parser.add_argument("--sample", type=int, default=12, help="Live sample size, 1-12.")
    parser.add_argument("--repeats", type=int, default=3, help="Fixture repetitions per mode.")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.02,
        help="Simulated network seconds per fixture fundamental.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Save local JSON measurements (prefer runtime/tmp).",
    )
    args = parser.parse_args()
    if not 1 <= args.sample <= 12 or args.repeats < 1 or args.delay < 0:
        parser.error("Use a sample of 1-12, positive repeats and nonnegative delay")
    temporary_root = ROOT / "runtime" / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    loaded = load_settings(ROOT)
    with tempfile.TemporaryDirectory(
        prefix="fundamental-benchmark-", dir=temporary_root
    ) as directory:
        temporary = Path(directory)
        result = (
            live_measurements(loaded, temporary, args.workers, args.sample)
            if args.live
            else fixture_measurements(loaded, temporary, args.repeats, args.delay)
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    # Avoid flooding the terminal with raw live snapshots.
    summary = copy.deepcopy(result)
    for row in summary["measurements"]:
        row.pop("snapshots", None)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
