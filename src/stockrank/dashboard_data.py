"""Read-only, report-bound data contract shared with the local React UI."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from stockrank.config import load_settings
from stockrank.data.sec import load_sec_concept_specs
from stockrank.presentation import (
    candidate_policy_summary,
    filings_for_completed_run,
    no_candidate_explanation,
    ranking_change_summary,
    relative_status_label,
    score_breakdown,
)
from stockrank.provider_comparison import load_provider_comparison_config
from stockrank.sec_financials import FORMULA_VERSION, formula_manifest
from stockrank.storage import Storage
from stockrank.summaries import market_context_leadership_order, sector_member_tickers
from stockrank.version import APP_VERSION


def json_value(value):
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, sqlite3.Row):
        value = dict(value)
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class ReportReader(Storage):
    """Reuse tested Storage reads within one read-only SQLite snapshot."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def _classification_summary(rows, field):
    summaries = []
    for name in sorted({getattr(row, field) for row in rows}):
        selected = [row for row in rows if getattr(row, field) == name]
        counts = Counter(row.classification for row in selected)
        values = [
            float(row.relative_difference)
            for row in selected
            if row.relative_difference is not None
        ]
        summaries.append(
            {
                field: name,
                **counts,
                "median_relative_difference": median(values) if values else None,
            }
        )
    return summaries


def load_dashboard(root: Path) -> dict:
    settings = load_settings(root)
    if not settings.database_path.exists():
        return {"run": None, "app_version": APP_VERSION}
    connection = sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return json_value(_read_dashboard(ReportReader(connection), settings))
    finally:
        connection.close()


def _read_dashboard(storage, settings):
    run = storage.latest_run()
    if not run:
        return {"run": None, "app_version": APP_VERSION}
    run_id = run["run_id"]
    config = json.loads(run["config_json"])
    runtime = config.get("runtime", {})
    preferences = config.get("preferences", {})
    app = config.get("app", {})
    scoring = config.get("scoring", {})
    results = storage.get_results(run_id)
    limit = int(app.get("top_candidate_limit", 10))
    candidates = [r for r in results if r["eligible"]][:limit]
    for row in results:
        row["score_breakdown"] = score_breakdown(row, scoring.get("overall", {}))
        row["score_tier"] = {
            "High relative score": "Top Tier",
            "Above-average relative score": "Upper Tier",
            "Relative watchlist": "Watchlist Tier",
            "Lower relative score": "Lower Tier",
        }.get(
            relative_status_label(row["recommendation"]),
            relative_status_label(row["recommendation"]),
        )
    cutoff = datetime.fromisoformat(run["completed_at"]) if run["completed_at"] else None
    valid_cutoff = cutoff is not None and cutoff.utcoffset() is not None
    filings = {}
    for row in candidates:
        disclosure = filings_for_completed_run(
            tuple(storage.get_sec_filings(row["ticker"])), cutoff
        )
        filings[row["ticker"]] = {
            "items": disclosure.filings[:4],
            "limitation": disclosure.limitation,
        }
    previous, limitations = storage.previous_comparable_run_assessment(run_id)
    changes = None
    if previous:
        previous_config = json.loads(previous["config_json"])
        changes = ranking_change_summary(
            results,
            storage.get_results(previous["run_id"]),
            current_limit=limit,
            previous_limit=int(previous_config.get("app", {}).get("top_candidate_limit", limit)),
        )
    active = {
        "profile": settings.profile_name,
        "horizon": settings.investment_horizon,
        "risk": settings.risk_tolerance,
        "universe_name": settings.raw["universe"]["name"],
        "universe_size": len(settings.universe),
        "model_version": settings.model_version,
    }
    mismatches = []
    for label, saved, current in (
        ("universe", run["universe_name"], active["universe_name"]),
        ("scoring model", run["model_version"], settings.model_version),
        ("profile", preferences.get("profile", "balanced"), settings.profile_name),
        ("horizon", preferences.get("investment_horizon", "medium"), settings.investment_horizon),
        ("risk", preferences.get("risk_tolerance", "moderate"), settings.risk_tolerance),
    ):
        if saved != current:
            mismatches.append(f"{label}: {saved} → {current}")
    report_shadow = storage.latest_provider_comparison_run(analysis_run_id=run_id)
    shadow_rows = (
        storage.get_provider_metric_comparisons(report_shadow.comparison_run_id)
        if report_shadow
        else []
    )
    current_shadow, progress, policy, policy_error = None, None, None, None
    try:
        policy = load_provider_comparison_config(settings)
        current_shadow = storage.latest_provider_comparison_run(
            full_universe_only=True,
            config_version=policy.version,
            universe_name=active["universe_name"],
        )
        progress = storage.provider_comparison_full_universe_dates(
            policy.version,
            universe_name=active["universe_name"],
            supported_formula_contract={
                "formula_version": FORMULA_VERSION,
                "formula_manifest": formula_manifest(
                    concept_specs=load_sec_concept_specs(settings)
                ),
            },
        )
    except ValueError as error:
        policy_error = str(error)
    health = []
    for provider in (
        "sec-edgar",
        "sec-submissions",
        "sec-companyfacts",
        "sec-financials",
        "provider-shadow",
    ):
        item = storage.get_provider_health(provider)
        if item:
            data = asdict(item)
            if provider == "provider-shadow":
                data["detail"] = "; ".join(
                    p for p in data["detail"].split("; ") if not p.startswith("full_dates=")
                )
            health.append({"provider": provider, **data})
        else:
            health.append({"provider": provider, "status": "Not checked"})
    financials = []
    if valid_cutoff:
        for result in results:
            snapshot = storage.latest_sec_financial_snapshot(
                result["ticker"], available_at=cutoff, built_at_or_before=cutoff
            )
            if snapshot:
                metrics = {(m.metric_name, m.period_kind): m for m in snapshot.metrics}
                applicable = [m for m in snapshot.metrics if m.quality != "excluded"]

                def value(name, period, metrics=metrics):
                    metric = metrics.get((name, period))
                    return metric.value if metric else None

                financials.append(
                    {
                        "ticker": result["ticker"],
                        "as_of": snapshot.as_of,
                        "formula_version": snapshot.formula_version,
                        "formula_manifest": snapshot.formula_manifest,
                        "coverage": sum(m.value is not None for m in applicable) / len(applicable)
                        if applicable
                        else 0,
                        "revenue_ttm": value("revenue", "ttm"),
                        "revenue_growth": value("revenue_growth", "annual"),
                        "net_margin": value("net_margin", "ttm"),
                    }
                )
    sectors = [
        {**s, "tickers": sector_member_tickers(results, s["sector"])}
        for s in runtime.get("market_summary", {}).get("sector_momentum_3m", [])
    ]
    # Deliberate allowlist: never expose raw settings, file paths, or SEC contact identity.
    run_public = {
        key: run[key]
        for key in (
            "run_id",
            "as_of",
            "provider",
            "model_version",
            "universe_name",
            "status",
            "completed_at",
            "started_at",
            "reproducibility_status",
        )
    }
    return {
        "app_version": APP_VERSION,
        "run": run_public,
        "results": results,
        "candidates": candidates,
        "preferences": preferences,
        "scoring": scoring,
        "manifest": json.loads(run["manifest_json"]) if run["manifest_json"] else None,
        "reproducibility_reasons": json.loads(run["reproducibility_reasons_json"]),
        "warnings": json.loads(run["warnings_json"]),
        "research": storage.get_research(run_id),
        "filings": filings,
        "market": [
            {"ticker": t, **v}
            for t, v in market_context_leadership_order(storage.get_market_context(run_id))
        ],
        "sectors": sectors,
        "policy": candidate_policy_summary(app, scoring.get("eligibility", {})),
        "empty_candidates": no_candidate_explanation(results, app, scoring.get("eligibility", {})),
        "previous": {key: previous[key] for key in ("run_id", "as_of")} if previous else None,
        "changes": changes,
        "comparison_limitations": limitations,
        "freshness": runtime.get("data_freshness", {}),
        "freshness_label": runtime.get("freshness_label", "Freshness unknown"),
        "scoring_quality": runtime.get("scoring_quality", {}),
        "active": active,
        "configuration_mismatches": mismatches,
        "provider_health": health,
        "financials": financials,
        "financial_cutoff_valid": valid_cutoff,
        "report_shadow": report_shadow,
        "shadow_rows": shadow_rows,
        "shadow_counts": Counter(r.classification for r in shadow_rows),
        "shadow_by_metric": _classification_summary(shadow_rows, "metric_name"),
        "shadow_by_sector": _classification_summary(shadow_rows, "sector"),
        "current_shadow": current_shadow,
        "shadow_progress": progress,
        "shadow_policy": policy.version if policy else None,
        "shadow_required": policy.required_full_universe_dates if policy else None,
        "shadow_policy_error": policy_error,
    }
