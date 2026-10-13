"""Analysis execution, stored-run validation, and qualitative research import."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from stockrank.config import load_settings
from stockrank.pipeline import run_analysis
from stockrank.reporting import write_report_bundle
from stockrank.research import normalize_research, validate_research
from stockrank.storage import Storage


def _command_run_analysis(args: argparse.Namespace) -> int:
    """Run and present one analysis without performing post-run validation."""
    settings = load_settings()
    run_id, report_path, warnings = run_analysis(settings, demo=args.demo, force=args.force)
    print(f"Run: {run_id}")
    print(f"Report: {report_path}")
    print(f"Warnings: {len(warnings)}")
    run = Storage(settings.database_path).latest_run()
    return int(not run or run["run_id"] != run_id or run["status"] != "completed")


def command_run(args: argparse.Namespace) -> int:
    analysis_result = _command_run_analysis(args)
    validation_result = command_validate(argparse.Namespace())
    return analysis_result or validation_result


def command_validate(_: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    run = storage.latest_run()
    if not run:
        print("No analysis run exists.")
        return 1
    results = storage.get_results(run["run_id"])
    research = storage.get_research(run["run_id"])
    priced = sum(result["latest_price"] is not None for result in results)
    eligible = sum(result["eligible"] for result in results)
    sparse = sum(
        result["overall_coverage"] < float(settings.raw["app"]["minimum_overall_coverage"])
        for result in results
    )
    warnings = json.loads(run["warnings_json"])
    run_config = json.loads(run["config_json"])
    runtime_metadata = run_config.get("runtime", {})
    if not isinstance(runtime_metadata, dict):
        runtime_metadata = {}
    freshness_value = runtime_metadata.get("data_freshness", {})
    freshness = freshness_value if isinstance(freshness_value, dict) else {}
    scoring_quality_value = runtime_metadata.get("scoring_quality", {})
    scoring_quality = scoring_quality_value if isinstance(scoring_quality_value, dict) else {}
    print(
        f"Latest run {run['run_id']} | status={run['status']} | as_of={run['as_of']} | "
        f"provider={run['provider']} | model={run['model_version']}"
    )
    print(
        f"Universe={len(results)} | priced={priced} | eligible={eligible} | "
        f"below_coverage_threshold={sparse} | warnings={len(warnings)}"
    )
    print(
        "Qualitative research="
        + ("imported" if research is not None else "not imported")
        + f" | run={run['run_id']}"
    )
    if scoring_quality:
        weak_value = scoring_quality.get("metrics_below_minimum", [])
        weak = (
            [value for value in weak_value if isinstance(value, str)]
            if isinstance(weak_value, list)
            else []
        )
        peer_counts_value = scoring_quality.get("metric_peer_counts", {})
        peer_counts = (
            {
                metric: count
                for metric, count in peer_counts_value.items()
                if isinstance(metric, str)
                and isinstance(count, (int, float))
                and not isinstance(count, bool)
            }
            if isinstance(peer_counts_value, dict)
            else {}
        )
        lowest_samples = sorted(peer_counts.items(), key=lambda item: (item[1], item[0]))[:5]
        print(
            "Metric peer minimum="
            f"{scoring_quality.get('minimum_metric_peer_count', 'unknown')} | "
            f"below_minimum={','.join(weak) if weak else 'none'}"
        )
        print(
            "Lowest metric peer samples="
            + (
                ",".join(f"{metric}:{count}" for metric, count in lowest_samples)
                if lowest_samples
                else "unavailable"
            )
        )
    if freshness:
        fundamental_values = freshness.get("fundamentals", {})
        if not isinstance(fundamental_values, dict):
            fundamental_values = {}
        fundamental_states = Counter(
            value.get("status", "unknown") if isinstance(value, dict) else "unknown"
            for value in fundamental_values.values()
        )
        fundamental_summary = (
            ",".join(f"{key}:{value}" for key, value in sorted(fundamental_states.items()))
            if fundamental_states
            else "unavailable"
        )
        print(
            f"Price refresh={freshness.get('price_refresh_status', 'unknown')} | "
            f"fundamentals={fundamental_summary}"
        )
    elif scoring_quality:
        print("Data freshness metadata=unavailable for this stored run")
    if run["provider"] == "demo-synthetic":
        print("DATA LABEL: SYNTHETIC DEMO — not suitable for investment research")
    for warning in warnings[:10]:
        print(f"WARNING: {warning}")
    if len(warnings) > 10:
        print(f"WARNING: {len(warnings) - 10} additional warnings are in the report/database")
    return 0 if run["status"] == "completed" and priced else 1


def command_research_import(args: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    errors = validate_research(payload, storage)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    payload = normalize_research(payload)
    storage.import_research(payload["run_id"], payload)
    report = write_report_bundle(settings, storage, payload["run_id"])
    print(f"Imported researched notes and refreshed report: {report}")
    return 0
