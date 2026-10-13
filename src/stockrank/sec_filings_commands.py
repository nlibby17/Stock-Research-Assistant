"""SEC identity health, filing synchronization, and filing coverage commands."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime

from stockrank.config import load_settings
from stockrank.data.sec import (
    SecClient,
    SecCompanyIdentity,
    SecError,
    SecIdentityDirectory,
    SecSubmissions,
    load_sec_entity_overrides,
    normalize_sec_ticker,
)
from stockrank.models import ProviderHealth
from stockrank.storage import Storage


def command_sec_health(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    endpoint = str(settings.raw.get("sec", {}).get("identity_url", ""))
    started = time.perf_counter()
    checked_at = datetime.now(UTC)
    try:
        directory = SecIdentityDirectory.from_settings(settings)
        snapshot = directory.fetch(force=bool(args.force))
        index = directory.index_by_ticker(snapshot.identities)
        matched = [
            security.ticker
            for security in settings.universe
            if normalize_sec_ticker(security.ticker) in index
        ]
        missing = [
            security.ticker for security in settings.universe if security.ticker not in matched
        ]
        status = "healthy" if not missing and not snapshot.stale else "degraded"
        details = [
            f"identity_records={len(snapshot.identities)}",
            f"universe_matches={len(matched)}/{len(settings.universe)}",
        ]
        if missing:
            details.append("missing=" + ",".join(missing))
        if snapshot.stale:
            details.append("stale_cache_fallback=true")
        latency_ms = (time.perf_counter() - started) * 1000
        health = ProviderHealth(
            provider="sec-edgar",
            checked_at=checked_at,
            status=status,
            endpoint=snapshot.source_url,
            latency_ms=latency_ms,
            cache_hit=snapshot.cache_hit,
            detail="; ".join(details),
        )
        storage.record_provider_health(health)
        print(
            f"SEC provider: {status} | identities={len(snapshot.identities)} | "
            f"universe={len(matched)}/{len(settings.universe)} | "
            f"cache={'stale' if snapshot.stale else 'hit' if snapshot.cache_hit else 'miss'} | "
            f"latency={latency_ms:.0f}ms"
        )
        if missing:
            print("Unmatched universe tickers: " + ", ".join(missing))
        print(f"Source: {snapshot.source_url}")
        print(f"Fetched at: {snapshot.fetched_at.isoformat()}")
        return 0 if status == "healthy" else 1
    except SecError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        storage.record_provider_health(
            ProviderHealth(
                provider="sec-edgar",
                checked_at=checked_at,
                status="unavailable",
                endpoint=endpoint,
                latency_ms=latency_ms,
                cache_hit=False,
                detail=str(exc),
            )
        )
        print(f"SEC provider: unavailable | {exc}", file=sys.stderr)
        return 2


def _years_ago(value: datetime, years: int) -> datetime:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _format_sec_document_counts(documents_checked: int, cache_hits: int) -> str:
    return (
        f"SEC documents checked={documents_checked} | cache hits={cache_hits} | "
        f"network downloads={documents_checked - cache_hits}"
    )


def command_sec_filings_sync(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    years = int(args.years or settings.raw.get("sec", {}).get("filing_history_years", 5))
    if years <= 0:
        print("ERROR: --years must be greater than zero.", file=sys.stderr)
        return 2
    since_date = _years_ago(datetime.now(UTC), years).date()
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
    started = time.perf_counter()
    checked_at = datetime.now(UTC)
    failures: list[str] = []
    stale_tickers: list[str] = []
    synced = 0
    stored_count = 0
    annual_coverage = 0
    quarterly_coverage = 0
    effective_count = 0
    cache_hits = 0
    documents_checked = 0
    endpoint = "https://data.sec.gov/submissions/CIK##########.json"
    try:
        client = SecClient.from_settings(settings)
        identity_directory = SecIdentityDirectory.from_settings(settings, client)
        identity_snapshot = identity_directory.fetch(force=bool(args.force))
        identities = identity_directory.index_by_ticker(identity_snapshot.identities)
        submissions = SecSubmissions.from_settings(settings, client)
        entity_overrides = load_sec_entity_overrides(settings)
    except SecError as exc:
        failures.append(f"identity setup: {exc}")
        identities = {}
        submissions = None
        entity_overrides = {}

    for security in selected:
        ticker = normalize_sec_ticker(security.ticker)
        identity = identities.get(ticker)
        if not identity:
            failures.append(f"{ticker}: no SEC identity")
            continue
        sync_identities = [identity]
        for additional_cik in entity_overrides.get(ticker, ()):
            if additional_cik == identity.cik:
                continue
            sync_identities.append(
                SecCompanyIdentity(
                    cik=additional_cik,
                    name=f"{identity.name} predecessor",
                    ticker=ticker,
                    exchange=identity.exchange,
                )
            )
        ticker_filings = {}
        ticker_snapshots = []
        try:
            assert submissions is not None
            for sync_identity in sync_identities:
                snapshot = submissions.fetch(
                    sync_identity,
                    ticker=ticker,
                    since_date=since_date,
                    force=bool(args.force),
                )
                ticker_snapshots.append(snapshot)
                for filing in snapshot.filings:
                    ticker_filings[(filing.cik, filing.accession_number)] = filing
            storage.replace_sec_filings(
                ticker=ticker,
                ciks=[value.cik for value in sync_identities],
                since_date=since_date,
                filings=ticker_filings.values(),
            )
        except SecError as exc:
            failures.append(f"{ticker}: {exc}")
            continue
        synced += 1
        stored_count += len(ticker_filings)
        effective = submissions.effective_filings(tuple(ticker_filings.values()))
        effective_count += len(effective)
        annual_coverage += any(filing.base_form == "10-K" for filing in effective)
        quarterly_coverage += any(filing.base_form == "10-Q" for filing in effective)
        cache_hits += sum(snapshot.cache_hits for snapshot in ticker_snapshots)
        documents_checked += sum(snapshot.documents_checked for snapshot in ticker_snapshots)
        if any(snapshot.stale for snapshot in ticker_snapshots):
            stale_tickers.append(ticker)

    status = (
        "healthy"
        if synced == len(selected)
        and annual_coverage == len(selected)
        and quarterly_coverage == len(selected)
        and not stale_tickers
        else "degraded"
    )
    latency_ms = (time.perf_counter() - started) * 1000
    details = [
        f"companies={synced}/{len(selected)}",
        f"filings={stored_count}",
        f"effective={effective_count}",
        f"annual_coverage={annual_coverage}/{len(selected)}",
        f"quarterly_coverage={quarterly_coverage}/{len(selected)}",
        f"since={since_date.isoformat()}",
    ]
    if stale_tickers:
        details.append("stale=" + ",".join(stale_tickers))
    if failures:
        details.append(f"failures={len(failures)}")
    full_universe_sync = len(selected) == len(settings.universe)
    health_status = status if full_universe_sync else "partial"
    details.append(f"scope={len(selected)}/{len(settings.universe)}")
    storage.record_provider_health(
        ProviderHealth(
            provider="sec-submissions",
            checked_at=checked_at,
            status=health_status if synced else "unavailable",
            endpoint=endpoint,
            latency_ms=latency_ms,
            cache_hit=bool(documents_checked and cache_hits == documents_checked),
            detail="; ".join(details),
        )
    )
    print(
        f"SEC filings: {status if synced else 'unavailable'} | "
        f"companies={synced}/{len(selected)} | filings={stored_count} | "
        f"effective={effective_count} | 10-K={annual_coverage}/{len(selected)} | "
        f"10-Q={quarterly_coverage}/{len(selected)} | since={since_date.isoformat()} | "
        f"latency={latency_ms:.0f}ms"
    )
    print(_format_sec_document_counts(documents_checked, cache_hits))
    for failure in failures[:10]:
        print(f"WARNING: {failure}")
    if len(failures) > 10:
        print(f"WARNING: {len(failures) - 10} additional failures were recorded")
    return 0 if status == "healthy" else 1


def command_sec_filings_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    health = storage.get_provider_health("sec-submissions")
    if not health:
        print("No SEC filing sync has been recorded. Run stockrank sec-filings-sync.")
        return 1
    tickers = [normalize_sec_ticker(security.ticker) for security in settings.universe]
    with_annual = 0
    with_quarterly = 0
    filing_count = 0
    for ticker in tickers:
        filings = storage.get_sec_filings(ticker)
        filing_count += len(filings)
        effective = SecSubmissions.effective_filings(tuple(filings))
        with_annual += any(filing.base_form == "10-K" for filing in effective)
        with_quarterly += any(filing.base_form == "10-Q" for filing in effective)
    print(
        f"SEC filings stored={filing_count} | 10-K coverage={with_annual}/{len(tickers)} | "
        f"10-Q coverage={with_quarterly}/{len(tickers)}"
    )
    status = (
        "healthy"
        if health.status == "healthy"
        and with_annual == len(tickers)
        and with_quarterly == len(tickers)
        else "partial"
    )
    print(f"Latest sync: {status} | checked={health.checked_at.isoformat()} | {health.detail}")
    return 0 if status == "healthy" else 1
