"""Offline command outcomes recorded before moving the SEC handlers."""

import importlib
from types import SimpleNamespace

import pytest

from stockrank import cli
from stockrank.data.sec import SecError
from stockrank.storage import Storage


def isolated_command(monkeypatch, tmp_path, argv):
    settings = SimpleNamespace(
        database_path=tmp_path / "test.sqlite3",
        universe=(SimpleNamespace(ticker="A", company="Alpha", sector="Technology"),),
        model_version="test-model",
        raw={"sec": {}, "app": {"timezone": "UTC"}},
    )
    args = cli.build_parser().parse_args(argv)
    owner = importlib.import_module(args.handler.__module__)
    monkeypatch.setattr(owner, "load_settings", lambda: settings)
    return settings, owner, args


@pytest.mark.parametrize(
    "command,message",
    [
        (
            "sec-filings-status",
            "No SEC filing sync has been recorded. Run stockrank sec-filings-sync.",
        ),
        (
            "sec-facts-status",
            "No SEC Company Facts sync has been recorded. Run stockrank sec-facts-sync.",
        ),
        (
            "sec-financials-status",
            "No SEC financial snapshots exist. Run stockrank sec-financials-build.",
        ),
    ],
)
def test_empty_sec_history_remains_explicit(monkeypatch, tmp_path, capsys, command, message):
    _, _, args = isolated_command(monkeypatch, tmp_path, [command])
    assert args.handler(args) == 1
    assert capsys.readouterr().out == message + "\n"


@pytest.mark.parametrize("command", ["sec-filings-sync", "sec-facts-sync", "sec-financials-build"])
def test_unknown_ticker_is_rejected_before_provider_access(monkeypatch, tmp_path, capsys, command):
    _, _, args = isolated_command(monkeypatch, tmp_path, [command, "--ticker", "UNKNOWN"])
    assert args.handler(args) == 2
    assert (
        capsys.readouterr().out == "ERROR: ticker(s) are not in the configured universe: UNKNOWN\n"
    )


@pytest.mark.parametrize("command", ["sec-filings-sync", "sec-facts-sync"])
def test_invalid_history_window_is_rejected(monkeypatch, tmp_path, capsys, command):
    _, _, args = isolated_command(monkeypatch, tmp_path, [command, "--years", "-1"])
    assert args.handler(args) == 2
    assert capsys.readouterr().err == "ERROR: --years must be greater than zero.\n"


@pytest.mark.parametrize(
    "command,dependency,provider,status",
    [
        ("sec-health", "SecIdentityDirectory", "sec-edgar", 2),
        ("sec-filings-sync", "SecClient", "sec-submissions", 1),
        ("sec-facts-sync", "SecClient", "sec-companyfacts", 1),
    ],
)
def test_unavailable_sec_provider_records_failure_and_preserves_exit_status(
    monkeypatch, tmp_path, capsys, command, dependency, provider, status
):
    settings, owner, args = isolated_command(monkeypatch, tmp_path, [command])

    def unavailable(*args, **kwargs):
        raise SecError("offline test failure")

    monkeypatch.setattr(getattr(owner, dependency), "from_settings", unavailable)
    assert args.handler(args) == status
    health = Storage(settings.database_path).get_provider_health(provider)
    assert health.status == "unavailable"
    assert health.cache_hit is False
    output = capsys.readouterr()
    assert "offline test failure" in output.out + output.err


def test_invalid_financial_cutoff_is_reported(monkeypatch, tmp_path, capsys):
    _, _, args = isolated_command(monkeypatch, tmp_path, ["sec-financials-build", "--as-of", "bad"])
    assert args.handler(args) == 2
    assert capsys.readouterr().err == "ERROR: --as-of must be an ISO date or datetime\n"


@pytest.mark.parametrize("command", ["provider-shadow-run", "provider-shadow-status"])
def test_invalid_comparison_config_remains_visible(monkeypatch, tmp_path, capsys, command):
    _, owner, args = isolated_command(monkeypatch, tmp_path, [command])

    def invalid(settings):
        raise ValueError("invalid comparison policy")

    monkeypatch.setattr(owner, "load_provider_comparison_config", invalid)
    assert args.handler(args) == 2
    assert capsys.readouterr().err == "ERROR: invalid comparison policy\n"
