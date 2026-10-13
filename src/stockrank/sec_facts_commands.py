"""SEC Company Facts refresh orchestration and stored coverage commands."""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import UTC, datetime

from stockrank.config import load_settings
from stockrank.data.sec import (
    SecClient,
    SecCompanyFacts,
    SecCompanyIdentity,
    SecError,
    SecIdentityDirectory,
    load_sec_concept_specs,
    load_sec_entity_overrides,
    normalize_sec_ticker,
)
from stockrank.models import ProviderHealth, SecCompanyFactsRefreshState
from stockrank.sec_filings_commands import _format_sec_document_counts, _years_ago
from stockrank.sec_refresh import (
    CompanyFactsRefreshPolicy,
    companyfacts_config_fingerprint,
    decide_companyfacts_refresh,
    filing_fingerprint,
    identity_fingerprint,
    latest_filing_at,
)
from stockrank.storage import Storage


def command_sec_facts_sync(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    sec_config = settings.raw.get("sec", {})
    years = int(args.years or sec_config.get("companyfacts_history_years", 5))
    if years <= 0:
        print("ERROR: --years must be greater than zero.", file=sys.stderr)
        return 2
    checked_at = datetime.now(UTC)
    since_date = _years_ago(checked_at, years).date()
    try:
        refresh_policy = CompanyFactsRefreshPolicy(
            full_refresh_hours=float(sec_config.get("companyfacts_full_refresh_hours", 168.0)),
            recent_filing_window_hours=float(
                sec_config.get("companyfacts_recent_filing_window_hours", 48.0)
            ),
            recent_filing_retry_hours=float(
                sec_config.get("companyfacts_recent_filing_retry_hours", 6.0)
            ),
        )
    except (TypeError, ValueError) as exc:
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
    core_concepts = tuple(
        str(value)
        for value in sec_config.get(
            "companyfacts_core_concepts",
            ("revenue", "net_income", "operating_cash_flow", "assets"),
        )
    )
    started = time.perf_counter()
    failures: list[str] = []
    stale_tickers: list[str] = []
    synced = 0
    stored_count = 0
    effective_count = 0
    full_core_coverage = 0
    concept_names = core_concepts
    cache_hits = 0
    documents_checked = 0
    refreshed_companies = 0
    reused_companies = 0
    refresh_reasons: Counter[str] = Counter()
    local_processing_ms = 0.0
    fetch_processing_ms = 0.0
    database_write_ms = 0.0
    unmatched_accessions = 0
    endpoint = "https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json"
    try:
        client = SecClient.from_settings(settings)
        directory = SecIdentityDirectory.from_settings(settings, client)
        identity_snapshot = directory.fetch(force=bool(args.force))
        identities = directory.index_by_ticker(identity_snapshot.identities)
        companyfacts = SecCompanyFacts.from_settings(settings, client)
        config_fingerprint = companyfacts_config_fingerprint(
            history_years=years,
            forms=companyfacts.forms,
            concepts=companyfacts.concept_specs,
        )
        concept_names = tuple(spec.canonical_name for spec in companyfacts.concept_specs)
        unknown_core = sorted(set(core_concepts) - set(concept_names))
        if unknown_core:
            raise SecError(
                "SEC Company Facts core concepts are not configured: " + ", ".join(unknown_core)
            )
        entity_overrides = load_sec_entity_overrides(settings)
    except SecError as exc:
        failures.append(f"Company Facts setup: {exc}")
        identities = {}
        companyfacts = None
        config_fingerprint = ""
        entity_overrides = {}
    concept_coverage = {concept: 0 for concept in concept_names}

    for security in selected:
        ticker = normalize_sec_ticker(security.ticker)
        identity = identities.get(ticker)
        if not identity:
            failures.append(f"{ticker}: no SEC identity")
            continue
        sync_identities = [identity]
        for additional_cik in entity_overrides.get(ticker, ()):
            if additional_cik != identity.cik:
                sync_identities.append(
                    SecCompanyIdentity(
                        cik=additional_cik,
                        name=f"{identity.name} predecessor",
                        ticker=ticker,
                        exchange=identity.exchange,
                    )
                )
        local_started = time.perf_counter()
        filings = tuple(storage.get_sec_filings(ticker, since_date=since_date))
        if not filings:
            failures.append(f"{ticker}: no stored SEC filings; run sec-filings-sync first")
            continue
        stored_facts = tuple(storage.get_sec_company_facts(ticker, since_date=since_date))
        state = storage.get_sec_companyfacts_refresh_state(ticker)
        ciks = [value.cik for value in sync_identities]
        current_identity_fingerprint = identity_fingerprint(ciks)
        current_filing_fingerprint = filing_fingerprint(filings)
        current_latest_filing_at = latest_filing_at(filings)
        decision = decide_companyfacts_refresh(
            now=checked_at,
            force=bool(args.force),
            has_local_facts=bool(stored_facts),
            state=state,
            current_identity_fingerprint=current_identity_fingerprint,
            current_filing_fingerprint=current_filing_fingerprint,
            current_config_fingerprint=config_fingerprint,
            current_latest_filing_at=current_latest_filing_at,
            policy=refresh_policy,
        )
        local_processing_ms += (time.perf_counter() - local_started) * 1000
        refresh_reasons[decision.reason] += 1
        facts = stored_facts
        ticker_unmatched_accessions = state.unmatched_accessions if state else 0
        snapshots = []
        if decision.refresh:
            ticker_facts = {}
            try:
                assert companyfacts is not None
                fetch_started = time.perf_counter()
                for sync_identity in sync_identities:
                    snapshot = companyfacts.fetch(
                        sync_identity,
                        ticker=ticker,
                        since_date=since_date,
                        filings=filings,
                        force=decision.bypass_raw_cache,
                    )
                    snapshots.append(snapshot)
                    for fact in snapshot.facts:
                        key = (
                            fact.cik,
                            fact.canonical_name,
                            fact.taxonomy,
                            fact.concept,
                            fact.unit,
                            fact.start_date,
                            fact.end_date,
                            fact.accession_number,
                        )
                        ticker_facts[key] = fact
                fetch_processing_ms += (time.perf_counter() - fetch_started) * 1000
                write_started = time.perf_counter()
                storage.replace_sec_company_facts(
                    ticker=ticker,
                    ciks=ciks,
                    since_date=since_date,
                    facts=ticker_facts.values(),
                )
                facts = tuple(ticker_facts.values())
                ticker_unmatched_accessions = sum(
                    snapshot.unmatched_accessions for snapshot in snapshots
                )
                if snapshots and not any(snapshot.stale for snapshot in snapshots):
                    storage.save_sec_companyfacts_refresh_state(
                        SecCompanyFactsRefreshState(
                            ticker=ticker,
                            identity_fingerprint=current_identity_fingerprint,
                            filing_fingerprint=current_filing_fingerprint,
                            config_fingerprint=config_fingerprint,
                            last_successful_refresh_at=min(
                                snapshot.fetched_at for snapshot in snapshots
                            ),
                            latest_filing_at=current_latest_filing_at,
                            unmatched_accessions=ticker_unmatched_accessions,
                            last_refresh_reason=decision.reason,
                        )
                    )
                database_write_ms += (time.perf_counter() - write_started) * 1000
            except SecError as exc:
                failures.append(f"{ticker}: {exc}")
                continue
            refreshed_companies += 1
            cache_hits += sum(snapshot.cache_hit for snapshot in snapshots)
            documents_checked += len(snapshots)
            if any(snapshot.stale for snapshot in snapshots):
                stale_tickers.append(ticker)
        else:
            reused_companies += 1
        synced += 1
        stored_count += len(facts)
        assert companyfacts is not None
        effective = companyfacts.effective_facts(facts)
        effective_count += len(effective)
        present = {fact.canonical_name for fact in effective}
        for concept in concept_names:
            concept_coverage[concept] += concept in present
        full_core_coverage += all(concept in present for concept in core_concepts)
        unmatched_accessions += ticker_unmatched_accessions

    status = (
        "healthy"
        if synced == len(selected)
        and full_core_coverage == len(selected)
        and not stale_tickers
        and unmatched_accessions == 0
        else "degraded"
    )
    latency_ms = (time.perf_counter() - started) * 1000
    details = [
        f"companies={synced}/{len(selected)}",
        f"facts={stored_count}",
        f"effective={effective_count}",
        f"core_complete={full_core_coverage}/{len(selected)}",
        f"since={since_date.isoformat()}",
        f"refreshed={refreshed_companies}",
        f"reused={reused_companies}",
        f"network_downloads={documents_checked - cache_hits}",
    ]
    details.extend(
        f"{concept}={coverage}/{len(selected)}" for concept, coverage in concept_coverage.items()
    )
    if unmatched_accessions:
        details.append(f"date_only_accessions={unmatched_accessions}")
    if stale_tickers:
        details.append("stale=" + ",".join(stale_tickers))
    if failures:
        details.append(f"failures={len(failures)}")
    full_universe_sync = len(selected) == len(settings.universe)
    health_status = status if full_universe_sync else "partial"
    details.append(f"scope={len(selected)}/{len(settings.universe)}")
    storage.record_provider_health(
        ProviderHealth(
            provider="sec-companyfacts",
            checked_at=checked_at,
            status=health_status if synced else "unavailable",
            endpoint=endpoint,
            latency_ms=latency_ms,
            cache_hit=bool(
                reused_companies == len(selected)
                or (documents_checked and cache_hits == documents_checked)
            ),
            detail="; ".join(details),
        )
    )
    print(
        f"SEC Company Facts: {status if synced else 'unavailable'} | "
        f"companies={synced}/{len(selected)} | facts={stored_count} | "
        f"effective={effective_count} | core={full_core_coverage}/{len(selected)} | "
        f"since={since_date.isoformat()} | latency={latency_ms:.0f}ms"
    )
    print(f"Refresh decisions: refreshed={refreshed_companies} | reused locally={reused_companies}")
    print(_format_sec_document_counts(documents_checked, cache_hits))
    print(
        f"Timing: local decisions/reads={local_processing_ms:.0f}ms | "
        f"SEC fetch/parse={fetch_processing_ms:.0f}ms | "
        f"database writes={database_write_ms:.0f}ms"
    )
    print("Refresh reasons:")
    for reason, count in sorted(refresh_reasons.items()):
        print(f"  {reason}: {count}")
    print("Metric coverage:")
    for concept, coverage in concept_coverage.items():
        print(f"  {concept}: {coverage}/{len(selected)}")
    if unmatched_accessions:
        print(f"WARNING: {unmatched_accessions} accession(s) use date-only availability")
    for failure in failures[:10]:
        print(f"WARNING: {failure}")
    if len(failures) > 10:
        print(f"WARNING: {len(failures) - 10} additional failures were recorded")
    return 0 if status == "healthy" else 1


def command_sec_facts_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    health = storage.get_provider_health("sec-companyfacts")
    if not health:
        print("No SEC Company Facts sync has been recorded. Run stockrank sec-facts-sync.")
        return 1
    tickers = [normalize_sec_ticker(security.ticker) for security in settings.universe]
    core_concepts = tuple(
        str(value) for value in settings.raw.get("sec", {}).get("companyfacts_core_concepts", ())
    )
    concept_names = tuple(spec.canonical_name for spec in load_sec_concept_specs(settings))
    concept_coverage = {concept: 0 for concept in concept_names}
    fact_count = 0
    complete = 0
    for ticker in tickers:
        facts = SecCompanyFacts.effective_facts(tuple(storage.get_sec_company_facts(ticker)))
        fact_count += len(facts)
        present = {fact.canonical_name for fact in facts}
        for concept in concept_names:
            concept_coverage[concept] += concept in present
        complete += all(concept in present for concept in core_concepts)
    print(f"SEC Company Facts effective={fact_count} | core coverage={complete}/{len(tickers)}")
    print("Concept coverage:")
    for key, value in concept_coverage.items():
        print(f"  {key}: {value}/{len(tickers)}")
    status = "healthy" if health.status == "healthy" and complete == len(tickers) else "partial"
    print(f"Latest sync: {status} | checked={health.checked_at.isoformat()} | {health.detail}")
    return 0 if status == "healthy" else 1
