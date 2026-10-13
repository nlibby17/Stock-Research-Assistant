"""Local SEC financial snapshot builds and coverage commands."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stockrank.config import load_settings
from stockrank.data.sec import load_sec_concept_specs, normalize_sec_ticker
from stockrank.models import ProviderHealth, SecFinancialMetric, SecFinancialSnapshot
from stockrank.sec_financials import FORMULA_VERSION, SecFinancialCalculator
from stockrank.storage import Storage


def _financial_as_of(value: str | None, timezone_name: str) -> datetime:
    if not value:
        return datetime.now(UTC)
    timezone = ZoneInfo(timezone_name)
    if len(value) == 10:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("--as-of must be an ISO date or datetime") from exc
        return datetime.combine(parsed_date, datetime_time.max, timezone).astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--as-of must be an ISO date or datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _financial_metric_lookup(
    snapshot: SecFinancialSnapshot,
) -> dict[tuple[str, str], SecFinancialMetric]:
    return {(metric.metric_name, metric.period_kind): metric for metric in snapshot.metrics}


def command_sec_financials_build(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    try:
        as_of = _financial_as_of(args.as_of, str(settings.raw["app"]["timezone"]))
    except (ValueError, ZoneInfoNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    requested = {
        normalize_sec_ticker(ticker)
        for raw in (args.ticker or [])
        for ticker in raw.split(",")
        if ticker.strip()
    }
    universe_by_ticker = {
        normalize_sec_ticker(security.ticker): security for security in settings.universe
    }
    unknown = sorted(requested - universe_by_ticker.keys())
    if unknown:
        print("ERROR: ticker(s) are not in the configured universe: " + ", ".join(unknown))
        return 2
    selected = [
        security
        for ticker, security in universe_by_ticker.items()
        if not requested or ticker in requested
    ]
    calculator = SecFinancialCalculator(concept_specs=load_sec_concept_specs(settings))
    started = time.perf_counter()
    failures: list[str] = []
    snapshots = []
    for security in selected:
        ticker = normalize_sec_ticker(security.ticker)
        facts = tuple(
            storage.get_sec_company_facts_as_of(ticker, as_of)
            if args.as_of is not None
            else storage.get_sec_company_facts(ticker)
        )
        if not facts:
            failures.append(f"{ticker}: no stored SEC Company Facts")
            continue
        try:
            snapshot = calculator.build_snapshot(
                ticker=ticker,
                company_name=facts[0].company_name or security.company,
                sector=security.sector,
                facts=facts,
                as_of=as_of,
            )
            storage.save_sec_financial_snapshot(snapshot)
        except (ValueError, ArithmeticError) as exc:
            failures.append(f"{ticker}: {exc}")
            continue
        snapshots.append(snapshot)

    coverage_keys = (
        ("revenue", "annual"),
        ("revenue", "quarter"),
        ("revenue", "ttm"),
        ("net_income", "ttm"),
        ("free_cash_flow", "ttm"),
        ("revenue_growth", "annual"),
        ("earnings_growth", "annual"),
        ("gross_margin", "ttm"),
        ("net_margin", "ttm"),
        ("return_on_equity", "ttm"),
        ("current_ratio", "instant"),
    )
    coverage = {key: 0 for key in coverage_keys}
    exclusions = {key: 0 for key in coverage_keys}
    for snapshot in snapshots:
        metrics = _financial_metric_lookup(snapshot)
        for key in coverage_keys:
            metric = metrics[key]
            coverage[key] += metric.value is not None
            exclusions[key] += metric.quality == "excluded"
    full_scope = len(selected) == len(settings.universe)
    status = "healthy" if len(snapshots) == len(selected) else "degraded"
    health_status = status if full_scope else "partial"
    latency_ms = (time.perf_counter() - started) * 1000
    coverage_detail = ",".join(
        f"{name}.{period}={coverage[(name, period)]}/{len(selected)}"
        for name, period in coverage_keys
    )
    details = (
        f"snapshots={len(snapshots)}/{len(selected)}; as_of={as_of.isoformat()}; "
        f"formula={FORMULA_VERSION}; {coverage_detail}; failures={len(failures)}; "
        f"scope={len(selected)}/{len(settings.universe)}"
    )
    storage.record_provider_health(
        ProviderHealth(
            provider="sec-financials",
            checked_at=datetime.now(UTC),
            status=health_status if snapshots else "unavailable",
            endpoint=f"local://{FORMULA_VERSION}",
            latency_ms=latency_ms,
            cache_hit=True,
            detail=details,
        )
    )
    print(
        f"SEC financial snapshots: {status if snapshots else 'unavailable'} | "
        f"built={len(snapshots)}/{len(selected)} | as_of={as_of.isoformat()} | "
        f"formula={FORMULA_VERSION} | latency={latency_ms:.0f}ms"
    )
    print("Metric coverage (excluded sector rows shown separately):")
    for key in coverage_keys:
        print(f"  {key[0]}.{key[1]}: {coverage[key]}/{len(selected)} (excluded={exclusions[key]})")
    for failure in failures[:10]:
        print(f"WARNING: {failure}")
    if len(failures) > 10:
        print(f"WARNING: {len(failures) - 10} additional failures were recorded")
    print(
        f"Ranking isolation: {settings.model_version} production scores were not read or changed."
    )
    return 0 if status == "healthy" else 1


def command_sec_financials_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    health = storage.get_provider_health("sec-financials")
    tickers = [normalize_sec_ticker(security.ticker) for security in settings.universe]
    snapshots = [
        snapshot
        for ticker in tickers
        if (snapshot := storage.latest_sec_financial_snapshot(ticker)) is not None
    ]
    if not snapshots:
        print("No SEC financial snapshots exist. Run stockrank sec-financials-build.")
        return 1
    complete = sum(snapshot.status == "complete" for snapshot in snapshots)
    manifested = sum(snapshot.formula_manifest is not None for snapshot in snapshots)
    print(
        f"SEC financial snapshots={len(snapshots)}/{len(tickers)} | "
        f"usable={complete}/{len(tickers)} | formula={FORMULA_VERSION} | "
        f"formula_manifests={manifested}/{len(snapshots)}"
    )
    if manifested < len(snapshots):
        print(
            "Historical note: snapshots without formula manifests remain readable "
            "but are legacy-limited."
        )
    if health:
        print(
            f"Latest build: {health.status} | checked={health.checked_at.isoformat()} | "
            f"{health.detail}"
        )
    print("Ranking isolation: these calculations are not production ranking inputs.")
    return 0 if len(snapshots) == len(tickers) and complete == len(tickers) else 1
