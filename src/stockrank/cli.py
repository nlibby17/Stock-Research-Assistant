"""CLI parser wiring, dispatch, and top-level error reporting."""

from __future__ import annotations

import argparse
import sys

from stockrank import (
    configuration_commands,
    provider_commands,
    report_commands,
    sec_facts_commands,
    sec_filings_commands,
    sec_financial_commands,
    storage_commands,
    workflow_commands,
)
from stockrank.command_parser import build_command_parser


def build_parser() -> argparse.ArgumentParser:
    return build_command_parser(
        {
            "configure": configuration_commands.command_configure,
            "config-check": configuration_commands.command_config_check,
            "setup-check": configuration_commands.command_setup_check,
            "daily-report": workflow_commands.command_daily_report,
            "morning": workflow_commands.command_morning,
            "run": report_commands.command_run,
            "validate-latest": report_commands.command_validate,
            "research-import": report_commands.command_research_import,
            "storage-status": storage_commands.command_storage_status,
            "storage-clean": storage_commands.command_storage_clean,
            "sec-health": sec_filings_commands.command_sec_health,
            "sec-filings-sync": sec_filings_commands.command_sec_filings_sync,
            "sec-filings-status": sec_filings_commands.command_sec_filings_status,
            "sec-facts-sync": sec_facts_commands.command_sec_facts_sync,
            "sec-facts-status": sec_facts_commands.command_sec_facts_status,
            "sec-financials-build": sec_financial_commands.command_sec_financials_build,
            "sec-financials-status": sec_financial_commands.command_sec_financials_status,
            "provider-shadow-run": provider_commands.command_provider_shadow_run,
            "provider-shadow-status": provider_commands.command_provider_shadow_status,
            "dashboard": workflow_commands.command_dashboard,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
