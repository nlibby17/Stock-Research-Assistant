"""Daily step assembly and morning/review/dashboard handoff commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stockrank.config import load_settings
from stockrank.configuration_commands import command_config_check
from stockrank.daily_workflow import launch_dashboard, run_daily_workflow
from stockrank.provider_commands import command_provider_shadow_run
from stockrank.report_commands import _command_run_analysis, command_validate
from stockrank.sec_facts_commands import command_sec_facts_sync
from stockrank.sec_filings_commands import command_sec_filings_sync, command_sec_health
from stockrank.sec_financial_commands import command_sec_financials_build


def command_daily_report(args: argparse.Namespace) -> int:
    """Run every deterministic daily step; qualitative research remains a handoff."""
    force = bool(args.force)
    steps = (
        ("Configuration validation", command_config_check, argparse.Namespace(live=False)),
        ("SEC identity health", command_sec_health, argparse.Namespace(force=force)),
        (
            "SEC filing sync",
            command_sec_filings_sync,
            argparse.Namespace(force=force, years=None, ticker=None),
        ),
        (
            "SEC Company Facts sync",
            command_sec_facts_sync,
            argparse.Namespace(force=force, years=None, ticker=None),
        ),
        (
            "SEC financial snapshot build",
            command_sec_financials_build,
            argparse.Namespace(as_of=None, ticker=None),
        ),
        (
            "Yahoo ranking and base report",
            _command_run_analysis,
            argparse.Namespace(demo=False, force=force),
        ),
        (
            "SEC/Yahoo shadow comparison",
            command_provider_shadow_run,
            argparse.Namespace(ticker=None),
        ),
        ("Final validation", command_validate, argparse.Namespace()),
    )
    return run_daily_workflow(
        steps, load_runtime_settings=load_settings, step_results=getattr(args, "step_results", None)
    )


def command_dashboard(args: argparse.Namespace) -> int:
    dashboard_path = Path(__file__).with_name("dashboard.py")
    return launch_dashboard(
        dashboard_path,
        platform_name=sys.platform,
        executable=sys.executable,
        ui=getattr(args, "ui", "react"),
    )


def command_morning(args: argparse.Namespace) -> int:
    """Run the deterministic daily report, then open its dashboard."""
    print("Morning workflow: checking the universe before building today's report.")
    from stockrank.universe_commands import run_due_discovery

    proposal_path = run_due_discovery()
    if proposal_path:
        from stockrank.universe_review_server import serve_review

        print("\nWeekly universe proposal ready. Opening your review before the dashboard.")
        serve_review(load_settings(), proposal_path, report_force=bool(args.force))
        return 0
    from contextlib import redirect_stderr, redirect_stdout

    from stockrank.universe_jobs import ProgressOutput, assess_workflow, verify_report

    steps = []
    output = ProgressOutput(sys.stdout)
    with redirect_stdout(output), redirect_stderr(output):
        report_result = command_daily_report(
            argparse.Namespace(force=bool(args.force), step_results=steps)
        )
    if report_result:
        try:
            from dataclasses import asdict

            from stockrank.dashboard_data import load_dashboard
            from stockrank.universe_discovery import atomic_json

            failed = assess_workflow(report_result, steps)
            settings = load_settings()
            receipt = {
                "version": settings.raw["universe"]["name"],
                "members": [asdict(s) for s in settings.universe],
            }
            run_id = verify_report(load_dashboard(settings.root), receipt)
            warnings = [
                "Rankings passed validation. Additional SEC checks need attention: "
                + ", ".join(failed)
            ] + list(dict.fromkeys(output.warnings))
            atomic_json(
                settings.runtime_dir / "universe" / f"report-status-{run_id}.json",
                {"run_id": run_id, "warnings": warnings},
            )
            print("\nRankings validated. Continuing with visible SEC warnings.")
        except (ValueError, OSError, KeyError) as exc:
            print(
                "Morning workflow stopped because the daily report requires attention; "
                f"the dashboard was not started. {exc}",
                file=sys.stderr,
            )
            return report_result
    print("\nDaily report complete. Launching the dashboard.")
    return command_dashboard(argparse.Namespace())
