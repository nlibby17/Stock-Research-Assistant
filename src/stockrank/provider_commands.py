"""Isolated provider comparisons and promotion-evidence status commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from stockrank.config import load_settings
from stockrank.data.sec import load_sec_concept_specs, normalize_sec_ticker
from stockrank.models import ProviderComparisonRun, ProviderHealth
from stockrank.provider_comparison import compare_provider_metrics, load_provider_comparison_config
from stockrank.provider_evidence import (
    ProductionResultEvidence,
    ProductionRunEvidence,
    SecFormulaContractEvidence,
    evaluate_promotion_evidence,
)
from stockrank.sec_financials import FORMULA_VERSION, formula_manifest
from stockrank.storage import PROVIDER_EVIDENCE_MAX_LINK_AGE_HOURS, Storage


def _formula_contract_summary(contracts: tuple[dict[str, object], ...]) -> str:
    if not contracts:
        return "none recorded (legacy evidence)"
    values = []
    for contract in contracts:
        manifest = contract.get("formula_manifest")
        fingerprint = manifest.get("fingerprint") if isinstance(manifest, dict) else None
        values.append(
            f"{contract.get('formula_version') or 'missing-version'}@"
            f"{str(fingerprint)[:12] if fingerprint else 'missing-manifest'}"
        )
    return ", ".join(values)


def command_provider_shadow_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    try:
        config = load_provider_comparison_config(settings)
    except ValueError as exc:
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
    comparison_run_id = uuid4().hex
    started_at = datetime.now(UTC)
    as_of = started_at
    timezone_name = str(settings.raw["app"]["timezone"])
    analysis_date = as_of.astimezone(ZoneInfo(timezone_name)).date()
    comparisons = []
    failures: list[str] = []
    formula_contract_evidence: list[SecFormulaContractEvidence] = []
    for security in selected:
        ticker = normalize_sec_ticker(security.ticker)
        sec_snapshot = storage.latest_sec_financial_snapshot(ticker, available_at=as_of)
        formula_contract_evidence.append(
            SecFormulaContractEvidence(
                ticker=ticker,
                formula_version=sec_snapshot.formula_version if sec_snapshot else None,
                formula_manifest=sec_snapshot.formula_manifest if sec_snapshot else None,
            )
        )
        yahoo_fundamental = storage.get_fundamental(
            ticker, settings.provider_name, fresh_only=False
        )
        try:
            comparisons.extend(
                compare_provider_metrics(
                    comparison_run_id=comparison_run_id,
                    ticker=ticker,
                    sector=security.sector,
                    as_of=as_of,
                    config=config,
                    sec_snapshot=sec_snapshot,
                    yahoo_fundamental=yahoo_fundamental,
                )
            )
        except ValueError as exc:
            failures.append(f"{ticker}: {exc}")
    expected_rows = len(selected) * len(config.metrics)
    status = "complete" if len(comparisons) == expected_rows and not failures else "failed"
    full_universe = len(selected) == len(settings.universe)
    analysis = storage.latest_run() if full_universe else None
    analysis_results = storage.get_results(analysis["run_id"]) if analysis else []
    supported_manifest = formula_manifest(concept_specs=load_sec_concept_specs(settings))
    supported_formula_contract = {
        "formula_version": FORMULA_VERSION,
        "formula_manifest": supported_manifest,
    }
    production_run = (
        ProductionRunEvidence(
            run_id=analysis["run_id"],
            status=analysis["status"],
            provider=analysis["provider"],
            universe_name=analysis["universe_name"],
            completed_at=(
                datetime.fromisoformat(analysis["completed_at"])
                if analysis["completed_at"]
                else None
            ),
            as_of=date.fromisoformat(analysis["as_of"]),
            warnings=tuple(json.loads(analysis["warnings_json"])),
        )
        if analysis
        else None
    )
    promotion = evaluate_promotion_evidence(
        full_universe=full_universe,
        comparison_status=status,
        comparison_as_of=as_of,
        stale_rows=sum(value.classification == "stale" for value in comparisons),
        production_run=production_run,
        production_results=tuple(
            ProductionResultEvidence(
                ticker=normalize_sec_ticker(result["ticker"]),
                price_as_of=(
                    date.fromisoformat(result["price_as_of"])
                    if result["price_as_of"] is not None
                    else None
                ),
            )
            for result in analysis_results
        ),
        expected_provider=settings.provider_name,
        expected_universe_name=str(settings.raw["universe"]["name"]),
        expected_tickers=frozenset(universe_by_ticker),
        formula_contract_evidence=tuple(formula_contract_evidence),
        supported_formula_version=FORMULA_VERSION,
        supported_formula_manifest=supported_manifest,
        max_link_age_hours=PROVIDER_EVIDENCE_MAX_LINK_AGE_HOURS,
    )
    analysis_run_id = promotion.analysis_run_id
    evidence_date = promotion.evidence_date
    evidence_qualified = promotion.qualified
    evidence_reason = promotion.reason
    run = ProviderComparisonRun(
        comparison_run_id=comparison_run_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        as_of=as_of,
        config_version=config.version,
        universe_name=str(settings.raw["universe"]["name"]),
        scope_count=len(selected),
        universe_size=len(settings.universe),
        full_universe=full_universe,
        status=status,
        warnings=tuple(failures),
        analysis_run_id=analysis_run_id,
        evidence_date=evidence_date,
        evidence_qualified=evidence_qualified,
        evidence_reason=evidence_reason,
        formula_contracts=promotion.formula_contracts,
    )
    storage.save_provider_comparison_run(run, comparisons)
    classifications = Counter(value.classification for value in comparisons)
    full_dates = storage.provider_comparison_full_universe_dates(
        config.version,
        universe_name=str(settings.raw["universe"]["name"]),
        supported_formula_contract=supported_formula_contract,
    )
    detail = "; ".join(
        [
            f"run={comparison_run_id}",
            f"scope={len(selected)}/{len(settings.universe)}",
            f"rows={len(comparisons)}/{expected_rows}",
            f"full_dates={full_dates}/{config.required_full_universe_dates}",
            f"evidence_date={evidence_date.isoformat() if evidence_date else 'none'}",
            f"evidence_qualified={str(evidence_qualified).lower()}",
            f"formula_contracts={_formula_contract_summary(promotion.formula_contracts)}",
            *(f"{key}={value}" for key, value in sorted(classifications.items())),
        ]
    )
    storage.record_provider_health(
        ProviderHealth(
            provider="provider-shadow",
            checked_at=run.completed_at,
            status=(
                "healthy"
                if status == "complete" and full_universe and evidence_qualified
                else "partial"
                if status == "complete"
                else "degraded"
            ),
            endpoint=f"local://{config.version}",
            latency_ms=(run.completed_at - run.started_at).total_seconds() * 1000,
            cache_hit=True,
            detail=detail,
        )
    )
    print(
        f"Provider shadow run: {status} | id={comparison_run_id} | "
        f"scope={len(selected)}/{len(settings.universe)} | rows={len(comparisons)}/{expected_rows}"
    )
    print(f"Config: {config.version} | command date: {analysis_date.isoformat()}")
    print(
        "Promotion evidence: "
        + (
            f"market-data date {evidence_date.isoformat()} | production run {analysis_run_id}"
            if evidence_qualified and evidence_date
            else f"not qualified | {evidence_reason}"
        )
    )
    print(f"SEC formula contracts: {_formula_contract_summary(promotion.formula_contracts)}")
    print("Classifications:")
    for classification, count in sorted(classifications.items()):
        print(f"  {classification}: {count}")
    print("By metric:")
    for metric_name in sorted({value.metric_name for value in comparisons}):
        metric_counts = Counter(
            value.classification for value in comparisons if value.metric_name == metric_name
        )
        detail = ", ".join(
            f"{classification}={count}" for classification, count in sorted(metric_counts.items())
        )
        print(f"  {metric_name}: {detail}")
    fallback_counts = Counter(
        value.fallback_candidate for value in comparisons if value.fallback_candidate
    )
    if fallback_counts:
        print("Fallback candidates for later review:")
        for fallback, count in sorted(fallback_counts.items()):
            print(f"  {fallback}: {count}")
    print(
        f"Promotion evidence: {full_dates}/{config.required_full_universe_dates} "
        "successful full-universe market-data dates"
    )
    material = [value for value in comparisons if value.classification == "materially_different"]
    if material:
        print("Material discrepancies:")
        for value in material[:15]:
            print(
                f"  {value.ticker} {value.metric_name}: SEC={value.sec_value} "
                f"Yahoo={value.yahoo_value} relative_diff={value.relative_difference}"
            )
        if len(material) > 15:
            print(f"  {len(material) - 15} additional material discrepancies are stored")
    for failure in failures:
        print(f"WARNING: {failure}")
    print(
        "Ranking isolation: run_results and production model "
        f"{settings.model_version} were not changed."
    )
    return 0 if status == "complete" and (not full_universe or evidence_qualified) else 1


def command_provider_shadow_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    try:
        config = load_provider_comparison_config(settings)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    run = storage.latest_provider_comparison_run(
        config_version=config.version,
        universe_name=str(settings.raw["universe"]["name"]),
    )
    if not run:
        print("No provider shadow run exists. Run stockrank provider-shadow-run.")
        return 1
    comparisons = storage.get_provider_metric_comparisons(run.comparison_run_id)
    classifications = Counter(value.classification for value in comparisons)
    supported_formula_contract = {
        "formula_version": FORMULA_VERSION,
        "formula_manifest": formula_manifest(concept_specs=load_sec_concept_specs(settings)),
    }
    full_dates = storage.provider_comparison_full_universe_dates(
        config.version,
        universe_name=str(settings.raw["universe"]["name"]),
        supported_formula_contract=supported_formula_contract,
    )
    print(
        f"Latest provider shadow run={run.comparison_run_id} | status={run.status} | "
        f"as_of={run.as_of.isoformat()} | scope={run.scope_count}/{run.universe_size}"
    )
    print(
        f"Evidence date={run.evidence_date.isoformat() if run.evidence_date else 'none'} | "
        f"qualified={run.evidence_qualified} | {run.evidence_reason}"
    )
    print(f"SEC formula contracts: {_formula_contract_summary(run.formula_contracts)}")
    for classification, count in sorted(classifications.items()):
        print(f"  {classification}: {count}")
    print("By metric:")
    for metric_name in sorted({value.metric_name for value in comparisons}):
        metric_counts = Counter(
            value.classification for value in comparisons if value.metric_name == metric_name
        )
        detail = ", ".join(
            f"{classification}={count}" for classification, count in sorted(metric_counts.items())
        )
        print(f"  {metric_name}: {detail}")
    print("By sector:")
    for sector in sorted({value.sector for value in comparisons}):
        sector_counts = Counter(
            value.classification for value in comparisons if value.sector == sector
        )
        detail = ", ".join(
            f"{classification}={count}" for classification, count in sorted(sector_counts.items())
        )
        print(f"  {sector}: {detail}")
    print(
        f"Promotion evidence: {full_dates}/{config.required_full_universe_dates} "
        "successful full-universe market-data dates"
    )
    print("Ranking isolation: provider shadow rows are not production ranking inputs.")
    return 0 if run.status == "complete" else 1
