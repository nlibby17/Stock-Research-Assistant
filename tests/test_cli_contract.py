"""Public dispatch and failure contracts captured before the CLI module split."""

import importlib
from argparse import Namespace
from types import SimpleNamespace

import pytest

from stockrank import cli

# Exercise each registered command without running providers or touching live state.
COMMANDS = [
    ("setup-check", [], "command_setup_check", {}),
    ("config-check", ["--live"], "command_config_check", {"live": True}),
    (
        "configure",
        ["--profile", "growth", "--yes"],
        "command_configure",
        {"profile": "growth", "yes": True},
    ),
    ("daily-report", ["--force"], "command_daily_report", {"force": True}),
    ("morning", [], "command_morning", {"force": False}),
    ("run", ["--demo", "--force"], "command_run", {"demo": True, "force": True}),
    ("validate-latest", [], "command_validate", {}),
    (
        "research-import",
        ["--file", "research.json"],
        "command_research_import",
        {"file": "research.json"},
    ),
    ("storage-status", [], "command_storage_status", {}),
    ("storage-clean", ["--apply"], "command_storage_clean", {"apply": True}),
    ("sec-health", ["--force"], "command_sec_health", {"force": True}),
    (
        "sec-filings-sync",
        ["--ticker", "A,B", "--ticker", "C", "--years", "2", "--force"],
        "command_sec_filings_sync",
        {"ticker": ["A,B", "C"], "years": 2, "force": True},
    ),
    ("sec-filings-status", [], "command_sec_filings_status", {}),
    (
        "sec-facts-sync",
        [],
        "command_sec_facts_sync",
        {"ticker": None, "years": None, "force": False},
    ),
    ("sec-facts-status", [], "command_sec_facts_status", {}),
    (
        "sec-financials-build",
        ["--as-of", "2026-09-01", "--ticker", "A"],
        "command_sec_financials_build",
        {"as_of": "2026-09-01", "ticker": ["A"]},
    ),
    ("sec-financials-status", [], "command_sec_financials_status", {}),
    (
        "provider-shadow-run",
        ["--ticker", "A,B"],
        "command_provider_shadow_run",
        {"ticker": ["A,B"]},
    ),
    ("provider-shadow-status", [], "command_provider_shadow_status", {}),
    ("dashboard", [], "command_dashboard", {"ui": "react"}),
    (
        "universe-review",
        ["--file", "proposal.json", "--no-open"],
        "command_universe_review",
        {"file": "proposal.json", "no_open": True},
    ),
    (
        "universe-configure",
        ["--size", "50", "--disable"],
        "command_universe_configure",
        {"target_size": 50, "enabled": False},
    ),
    ("universe-status", [], "command_universe_status", {}),
    ("universe-preview", ["--open"], "command_universe_preview", {"open": True}),
    (
        "universe-approve",
        ["--file", "proposal.json", "--profile", "best_overall", "--yes"],
        "command_universe_approve",
        {"file": "proposal.json", "profile": "best_overall", "yes": True},
    ),
    (
        "universe-reject",
        ["--file", "proposal.json"],
        "command_universe_reject",
        {"file": "proposal.json"},
    ),
]


@pytest.mark.parametrize("command,options,name,values", COMMANDS)
def test_every_command_dispatches_once_and_preserves_exit_status(
    monkeypatch, command, options, name, values
):
    args = cli.build_parser().parse_args([command, *options])
    assert args.handler.__name__ == name
    owner = importlib.import_module(args.handler.__module__)
    calls = []

    def handler(namespace):
        calls.append(namespace)
        return 7

    monkeypatch.setattr(owner, name, handler)
    assert cli.main([command, *options]) == 7
    assert len(calls) == 1
    assert calls[0].command == command
    for key, value in values.items():
        assert getattr(calls[0], key) == value


@pytest.mark.parametrize(
    "error", [OSError("unavailable"), KeyError("missing"), ValueError("invalid")]
)
def test_main_preserves_handled_error_output_and_status(monkeypatch, capsys, error):
    def fail(args):
        raise error

    monkeypatch.setattr(
        cli, "build_parser", lambda: SimpleNamespace(parse_args=lambda _: Namespace(handler=fail))
    )
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: {error}\n"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["research-import"],
        ["dashboard", "--ui", "unknown"],
        ["configure", "--tickers", "A", "--use-default-universe"],
    ],
)
def test_parser_errors_still_exit_two(argv, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2
    assert "usage: stockrank" in capsys.readouterr().err


@pytest.mark.parametrize("command", [None, *(row[0] for row in COMMANDS)])
def test_help_exits_successfully_without_running_commands(command, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(([command] if command else []) + ["--help"])
    assert error.value.code == 0
    assert "usage: stockrank" in capsys.readouterr().out
