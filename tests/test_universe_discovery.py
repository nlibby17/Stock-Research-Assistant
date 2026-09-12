"""Synthetic fixtures exercise time, selection, failure and activation without live changes."""

import copy
import json
import shutil
from argparse import Namespace
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from stockrank import cli, customization
from stockrank import universe_commands as commands
from stockrank.config import VALID_SECTORS, load_settings
from stockrank.models import PriceBar
from stockrank.universe_discovery import (
    DiscoveryPolicy,
    build_proposal,
    digest,
    discovery_due,
    select_members,
    verify_proposal,
)
from stockrank.universe_review import render_review
from stockrank.universe_sources import (
    history_checks,
    listing_exclusions,
    listing_rows,
    submission_checks,
)

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path):
    source = Path(__file__).resolve().parents[1] / "config"
    shutil.copytree(source, tmp_path / "config", ignore=shutil.ignore_patterns("*.local.*"))
    return load_settings(tmp_path)


@pytest.fixture
def evidence(settings):
    rows = []
    for index, sector in enumerate(sorted(VALID_SECTORS)):
        for number in range(8):
            rows.append(
                {
                    "ticker": f"T{index:02}{number}",
                    "company": f"Fixture {index}-{number}",
                    "sector": sector,
                    "cik": f"{index * 8 + number + 1:010}",
                    "score": 100 - index * 5 - number,
                    "coverage": 0.9,
                    "exclusions": [],
                    "warnings": [],
                }
            )
    for security in settings.universe:
        rows.append(
            {
                **asdict(security),
                "cik": "9999999999",
                "score": None,
                "coverage": None,
                "exclusions": ["Synthetic exclusion for test only"],
                "warnings": [],
            }
        )
    return {"candidates": rows, "errors": [], "market_date": "2026-09-08"}


@pytest.mark.parametrize(
    "days,expected", [(0, False), (1, False), (6, False), (7, True), (8, True)]
)
def test_weekly_due_without_waiting(days, expected):
    assert discovery_due(NOW.isoformat(), NOW + timedelta(days=days)) is expected


def test_first_run_and_exact_boundary():
    assert discovery_due(None, NOW)
    assert not discovery_due(NOW.isoformat(), NOW + timedelta(days=7, microseconds=-1))
    with pytest.raises(ValueError):
        discovery_due((NOW + timedelta(seconds=1)).isoformat(), NOW)


def test_profiles_use_same_pool_and_diversified_balances_counts(evidence):
    rows = evidence["candidates"]
    best = select_members(rows, "best_overall", 50)
    diverse = select_members(rows, "diversified", 50)
    assert len(best) == len(diverse) == 50
    assert best[0]["score"] == 100
    assert min(r["score"] for r in best) >= max(
        r["score"] for r in rows if r["score"] is not None and r not in best
    )
    counts = [sum(r["sector"] == sector for r in diverse) for sector in VALID_SECTORS]
    assert min(counts) == 4 and max(counts) == 5
    assert len({r["sector"] for r in best}) < 11
    assert select_members(list(reversed(rows)), "diversified", 50) == diverse


def test_ties_and_duplicate_issuer_are_deterministic(evidence):
    rows = copy.deepcopy(evidence["candidates"][:3])
    for row in rows:
        row["score"] = 50
    rows[1]["cik"] = rows[0]["cik"]
    selected = select_members(list(reversed(rows)), "best_overall", 3)
    assert [r["ticker"] for r in selected] == [rows[0]["ticker"], rows[2]["ticker"]]


def test_insufficient_sectors_and_provider_errors_block_activation(settings, evidence):
    evidence["errors"] = ["Provider unavailable"]
    for row in evidence["candidates"]:
        if row["sector"] == "Utilities":
            row["exclusions"].append("Unavailable")
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    assert "Provider unavailable" in proposal["profiles"]["best_overall"]["blockers"]
    assert any("11 sectors" in s for s in proposal["profiles"]["diversified"]["blockers"])
    with pytest.raises(ValueError, match="cannot be activated"):
        commands.approve_proposal(settings, proposal, "diversified", now=NOW)


def test_preview_does_not_advance_schedule_or_change_config(settings, evidence, monkeypatch):
    monkeypatch.setattr(commands, "collect_evidence", lambda *a, **k: evidence)
    before = {p: p.read_bytes() for p in (settings.root / "config").iterdir() if p.is_file()}
    path = commands.run_discovery(settings, DiscoveryPolicy(), preview=True, now=NOW)
    assert path.exists() and path.with_suffix(".html").exists()
    assert commands.schedule_state(settings) == {}
    assert all(p.read_bytes() == content for p, content in before.items())
    assert not settings.local_preferences_path.exists()
    assert not settings.database_path.exists()


def test_weekly_schedule_success_failure_retry_and_context_change(settings, evidence, monkeypatch):
    calls = []

    def collect(*args, **kwargs):
        calls.append(1)
        return evidence

    monkeypatch.setattr(commands, "collect_evidence", collect)
    policy = DiscoveryPolicy()
    first = commands.run_discovery(settings, policy, preview=False, now=NOW)
    state = commands.schedule_state(settings)
    last = datetime.fromisoformat(next(iter(state.values()))["last_success"])
    assert (
        first
        and commands.run_discovery(settings, policy, preview=False, now=last + timedelta(days=6))
        is None
    )
    commands.run_discovery(settings, policy, preview=False, now=last + timedelta(days=7))
    assert len(calls) == 2
    commands.run_discovery(
        settings,
        replace(policy, profile="best_overall"),
        preview=False,
        now=last + timedelta(days=8),
    )
    assert len(calls) == 3
    # Provider errors generate diagnostics but leave a new context due for retry.
    changed = replace(policy, per_sector=11)
    evidence["errors"] = ["Network failure"]
    old = commands.schedule_state(settings)
    commands.run_discovery(settings, changed, preview=False, now=last + timedelta(days=9))
    assert commands.schedule_state(settings) == old
    evidence["errors"] = []
    commands.run_discovery(settings, changed, preview=False, now=last + timedelta(days=10))
    assert len(commands.schedule_state(settings)) == len(old) + 1


def test_provider_exception_releases_lock_and_leaves_schedule_unchanged(settings, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("Network unavailable")

    monkeypatch.setattr(commands, "collect_evidence", fail)
    with pytest.raises(RuntimeError):
        commands.run_discovery(settings, DiscoveryPolicy(), preview=False, now=NOW)
    assert commands.schedule_state(settings) == {}
    assert not (commands.directory(settings) / "operation.lock").exists()


@pytest.mark.parametrize("profile", ["best_overall", "diversified"])
def test_approval_preserves_weekly_clock_across_fresh_starts(
    settings, evidence, monkeypatch, profile
):
    calls = []

    def collect(*args, **kwargs):
        calls.append(1)
        return evidence

    monkeypatch.setattr(commands, "collect_evidence", collect)
    path = commands.run_discovery(settings, DiscoveryPolicy(), preview=False, now=NOW)
    proposal = commands.read_proposal(path)
    protected = settings.universe[0].ticker
    commands.approve_proposal(
        settings, proposal, profile, now=NOW + timedelta(hours=1), protect=[protected]
    )
    # Read the on-disk state afresh as both launcher and manually started morning do.
    for elapsed in (timedelta(hours=2), timedelta(days=6, hours=23)):
        active = load_settings(settings.root)
        assert (
            commands.run_discovery(
                active, commands.load_policy(active), preview=False, now=NOW + elapsed
            )
            is None
        )
    assert len(calls) == 1
    active = load_settings(settings.root)
    assert commands.run_discovery(
        active, commands.load_policy(active), preview=False, now=NOW + timedelta(days=7)
    )
    assert len(calls) == 2


def test_approval_clock_does_not_hide_later_policy_changes(settings, evidence, monkeypatch):
    monkeypatch.setattr(commands, "collect_evidence", lambda *a, **k: evidence)
    path = commands.run_discovery(settings, DiscoveryPolicy(), preview=False, now=NOW)
    commands.approve_proposal(settings, commands.read_proposal(path), "best_overall", now=NOW)
    active = load_settings(settings.root)
    policy = replace(commands.load_policy(active), per_sector=11)
    assert commands.run_discovery(active, policy, preview=False, now=NOW + timedelta(hours=1))
    assert not (commands.directory(settings) / "operation.lock").exists()


def test_disabled_weekly_does_not_fetch_but_preview_still_works(settings, evidence, monkeypatch):
    monkeypatch.setattr(commands, "collect_evidence", lambda *a, **k: evidence)
    policy = DiscoveryPolicy(enabled=False)
    assert commands.run_discovery(settings, policy, preview=False, now=NOW) is None
    assert commands.run_discovery(settings, policy, preview=True, now=NOW)


def test_rejection_preserves_membership_and_history(settings, evidence, monkeypatch):
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    path = settings.root / "proposal.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    monkeypatch.setattr(commands, "load_settings", lambda: settings)
    settings.runtime_dir.mkdir()
    settings.database_path.write_bytes(b"immutable historical database fixture")
    before = settings.database_path.read_bytes()
    assert commands.command_universe_reject(Namespace(file=str(path))) == 0
    assert load_settings(settings.root).universe == settings.universe
    assert settings.database_path.read_bytes() == before
    with pytest.raises(ValueError, match="already has a decision"):
        commands.approve_proposal(settings, proposal, "best_overall", now=NOW)


def test_approval_exact_membership_preserves_custom_settings_and_history(settings, evidence):
    personal = '# Keep this comment\n[provider]\nrequest_retries = 1\n\n[preferences]\nprofile = "balanced"\n'
    settings.local_preferences_path.write_text(personal, encoding="utf-8")
    settings = load_settings(settings.root)
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    settings.runtime_dir.mkdir()
    settings.database_path.write_bytes(b"immutable historical database fixture")
    version = commands.approve_proposal(settings, proposal, "diversified", now=NOW)
    activated = load_settings(settings.root)
    assert [asdict(s) for s in activated.universe] == proposal["profiles"]["diversified"]["members"]
    assert activated.raw["universe"]["name"] == version
    assert activated.raw["scoring"] == settings.raw["scoring"]
    assert activated.raw["provider"] == settings.raw["provider"]
    assert settings.local_preferences_path.read_text(encoding="utf-8").startswith(personal.rstrip())
    assert settings.database_path.read_bytes() == b"immutable historical database fixture"
    assert (commands.directory(settings) / f"decision-{proposal['id']}.json").exists()


def test_activation_failure_rolls_back_all_files(settings, evidence, monkeypatch):
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    original = customization._install_staged_file

    def fail_receipt(staged, path):
        if path.name.startswith("decision-"):
            raise OSError("Simulated full disk")
        original(staged, path)

    monkeypatch.setattr(customization, "_install_staged_file", fail_receipt)
    with pytest.raises(customization.PersonalizationUpdateError, match="restored"):
        commands.approve_proposal(settings, proposal, "best_overall", now=NOW)
    assert load_settings(settings.root).universe == settings.universe
    assert not settings.local_preferences_path.exists()
    assert not list(commands.directory(settings).glob("decision-*.json"))


def test_modified_expired_and_changed_context_proposals_are_rejected(settings, evidence):
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    with pytest.raises(ValueError, match="expired"):
        commands.approve_proposal(settings, proposal, "best_overall", now=NOW + timedelta(days=3))
    changed = replace(settings, universe=settings.universe[:-1])
    with pytest.raises(ValueError, match="changed"):
        commands.approve_proposal(changed, proposal, "best_overall", now=NOW)
    proposal["profiles"]["best_overall"]["members"].pop()
    with pytest.raises(ValueError, match="changed"):
        verify_proposal(proposal)
    proposal["id"] = digest({k: v for k, v in proposal.items() if k != "id"})
    with pytest.raises(ValueError, match="does not match"):
        commands.approve_proposal(settings, proposal, "best_overall", now=NOW)


def test_review_escapes_external_text(settings, evidence):
    evidence["candidates"][0]["company"] = '<script>alert("unsafe")</script>'
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    html = render_review(proposal)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Best overall" in html and "Diversified" in html


def test_listing_scope_and_stale_directory():
    text = (
        "Symbol|Security Name|ETF|Test Issue|Financial Status\n"
        "ABC|ABC Common Stock|N|N|N\nFile Creation Time: 0908202617:03||||\n"
    )
    row = listing_rows(text, nasdaq=True, now=NOW)["ABC"]
    assert listing_exclusions(row) == []
    assert listing_exclusions({**row, "ETF": "Y"})
    assert listing_exclusions({**row, "Financial Status": "D"})
    assert listing_exclusions({**row, "Security Name": "ABC Preferred Stock"})
    assert listing_exclusions(None)
    with pytest.raises(ValueError, match="stale"):
        listing_rows(text, nasdaq=True, now=NOW + timedelta(days=10))


@pytest.mark.parametrize("form", ["20-F", "40-F", "6-K", "10-KT"])
def test_foreign_and_corporate_actions_fail_closed(form):
    payload = {
        "tickers": ["ABC"],
        "sic": "1000",
        "filings": {"recent": {"form": ["10-K", form], "filingDate": ["2026-02-01", "2026-08-01"]}},
    }
    reasons, _ = submission_checks(payload, "ABC", today=date(2026, 9, 9))
    assert reasons


@pytest.mark.parametrize("form", ["25", "15-12B", "S-4", "DEFM14A"])
def test_generic_corporate_forms_warn_without_claiming_common_stock_delisting(form):
    payload = {
        "tickers": ["ABC"],
        "sic": "1000",
        "filings": {"recent": {"form": ["10-K", form], "filingDate": ["2026-02-01", "2026-08-01"]}},
    }
    reasons, warnings = submission_checks(payload, "ABC", today=NOW.date())
    assert not reasons and warnings


def test_history_annual_report_can_confirm_high_volume_filer():
    payload = {
        "tickers": ["ABC"],
        "sic": "1000",
        "filings": {"recent": {"form": ["8-K"], "filingDate": ["2026-08-01"]}},
    }
    assert submission_checks(payload, "ABC", today=NOW.date())[0]
    assert not submission_checks(payload, "ABC", today=NOW.date(), annual_history_confirmed=True)[0]


def test_sec_identity_spac_and_missing_history():
    payload = {
        "tickers": ["ABC"],
        "sic": "6770",
        "filings": {"recent": {"form": ["10-K"], "filingDate": ["2026-02-01"]}},
    }
    assert submission_checks(payload, "ABC", today=NOW.date())[0]
    payload["sic"] = "1000"
    assert not submission_checks(payload, "ABC", today=NOW.date())[0]
    assert submission_checks(payload, "XYZ", today=NOW.date())[0]


def test_morning_discovery_failure_does_not_block_report_dashboard(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "command_daily_report", lambda args: calls.append("report") or 0)
    monkeypatch.setattr(cli, "command_dashboard", lambda args: calls.append("dashboard") or 0)

    def fail():
        calls.append("discovery")
        raise ValueError("Invalid provider state")

    monkeypatch.setattr(commands, "load_settings", fail)
    assert cli.command_morning(Namespace(force=False)) == 0
    assert calls == ["discovery", "report", "dashboard"]


def test_cli_profile_and_preview_are_separate_from_scoring():
    parser = cli.build_parser()
    args = parser.parse_args(["universe-configure", "--profile", "best_overall", "--size", "55"])
    assert args.profile == "best_overall" and args.target_size == 55 and args.enabled is None
    assert parser.parse_args(["universe-preview", "--open"]).open


def test_history_thresholds_gaps_and_missing_volume():
    days = [NOW.date() - timedelta(days=offset) for offset in range(500, 0, -1)]
    sessions = [day for day in days if day.weekday() < 5][-300:]
    bars = [
        PriceBar("TEST", day, None, None, None, 5, 5, 2_000_000, "fixture", NOW) for day in sessions
    ]
    assert history_checks(bars, sessions) == ([], 10_000_000)
    assert history_checks(bars[1:], sessions)[0]
    assert history_checks(bars[:-10] + bars[-9:], sessions)[0]
    assert history_checks(bars[:-1], sessions)[0]
    assert history_checks([replace(b, close=4.99) for b in bars], sessions)[0]
    assert history_checks([replace(b, volume=1_999_999) for b in bars], sessions)[0]
    assert history_checks(bars[:-1] + [replace(bars[-1], volume=None)], sessions)[0]


def test_overlapping_operation_is_refused(settings):
    with (
        commands.discovery_lock(settings),
        pytest.raises(ValueError, match="Another universe operation"),
        commands.discovery_lock(settings),
    ):
        pytest.fail("Second operation must not acquire the lock")


def test_noninteractive_approval_requires_explicit_confirmation(settings, evidence, monkeypatch):
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    path = settings.root / "proposal.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")
    monkeypatch.setattr(commands, "load_settings", lambda: settings)
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="explicit --yes"):
        commands.command_universe_approve(Namespace(file=path, profile="best_overall", yes=False))
    assert not settings.local_preferences_path.exists()


def test_activation_replaces_only_existing_universe_table(settings, evidence):
    personal = (
        '# preserve\n[universe]\nname = "us_diversified_50_v1"\n'
        'path = "config/universe.csv"\n\n[provider]\nrequest_retries = 1\n'
    )
    settings.local_preferences_path.write_text(personal, encoding="utf-8")
    settings = load_settings(settings.root)
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    commands.approve_proposal(settings, proposal, "best_overall", now=NOW)
    result = settings.local_preferences_path.read_text(encoding="utf-8")
    assert result.startswith("# preserve\n")
    assert result.endswith("[provider]\nrequest_retries = 1\n")
    assert result.count("[universe]") == 1


@pytest.fixture
def review_http(settings, evidence):
    import http.client
    import threading
    from urllib.parse import urlencode

    from stockrank.universe_review_server import make_review_server

    proposal = build_proposal(
        settings, DiscoveryPolicy(), evidence, now=datetime.now(UTC), preview=True
    )
    path = settings.root / "review.json"
    path.write_text(json.dumps(proposal), encoding="utf-8")

    class FakeJob:
        def __init__(self, settings):
            self.kind = None
            self.result = {"status": "idle"}
            self.calls = []

        def start(self, kind, payload):
            self.kind = kind
            self.calls.append((kind, payload))
            self.result = {"status": "running"}

        def poll(self):
            return self.result

        def close(self):
            pass

    job = FakeJob(settings)
    server = make_review_server(settings, path, job_factory=lambda settings: job)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method="GET", path="/", fields=None, origin=True, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {}
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{server.server_port}"
        if host:
            headers["Host"] = host
        body = None
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(fields, doseq=True)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        request.location = response.getheader("Location")
        result = response.status, response.read().decode()
        connection.close()
        return result

    request.job = job
    request.server = server
    yield request, proposal
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_review_http_requires_confirmation_and_records_exact_approval(settings, review_http):
    import re

    request, proposal = review_http
    status, html = request()
    assert status == 200
    assert "Proposed removals (50)" in html and 'class="remove"' in html
    assert "Review &amp; approve Diversified" in html
    token = re.search(r'name="token" value="([^"]+)"', html)[1]
    assert request("POST", "/decision", {"token": token})[0] == 409
    status, confirmation = request(
        "POST", "/confirm", {"token": token, "action": "approve", "profile": "diversified"}
    )
    assert status == 200 and "Exact future membership" in confirmation
    assert not settings.local_preferences_path.exists()
    nonce = re.search(r'name="confirmation" value="([^"]+)"', confirmation)[1]
    assert request("POST", "/decision", {"token": token, "confirmation": nonce})[0] == 303
    assert [asdict(s) for s in load_settings(settings.root).universe] == proposal["profiles"][
        "diversified"
    ]["members"]
    assert "Decision recorded: approved" in request()[1]
    assert request("POST", "/decision", {"token": token, "confirmation": nonce})[0] == 409


def test_review_http_rejection_and_request_boundaries(settings, review_http):
    import re

    request, _ = review_http
    assert request(host="untrusted.example")[0] == 403
    assert request(path="/../../.env")[0] == 404
    assert request("POST", "/confirm", {}, origin=False)[0] == 403
    assert request("POST", "/confirm", {"token": "invalid"})[0] == 403
    token = re.search(r'name="token" value="([^"]+)"', request()[1])[1]
    _, html = request(
        "POST", "/confirm", {"token": token, "action": "reject", "profile": "best_overall"}
    )
    nonce = re.search(r'name="confirmation" value="([^"]+)"', html)[1]
    assert request("POST", "/decision", {"token": token, "confirmation": nonce})[0] == 303
    assert not settings.local_preferences_path.exists()
    assert "Decision recorded: rejected" in request()[1]
    assert "Review &amp; approve" not in request()[1]


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("launcher_first", [False, True])
def test_decision_runs_eight_steps_once_and_hands_off_one_dashboard(
    settings, review_http, monkeypatch, action, launcher_first
):
    import re

    from stockrank import universe_jobs
    from stockrank.pipeline import run_analysis

    request, _ = review_http
    config_before = {p: p.read_bytes() for p in (settings.root / "config").iterdir() if p.is_file()}
    token = re.search(r'name="token" value="([^"]+)"', request()[1])[1]
    _, html = request(
        "POST",
        "/confirm",
        {
            "token": token,
            "action": action,
            "profile": "diversified",
        },
    )
    assert not request.job.calls
    nonce = re.search(r'name="confirmation" value="([^"]+)"', html)[1]
    fields = {"token": token, "confirmation": nonce}
    assert request("POST", "/decision", fields)[0] == 303
    assert request.location == "/progress"
    assert len(request.job.calls) == 1
    assert request("POST", "/decision", fields)[0] == 409
    assert len(request.job.calls) == 1
    if action == "reject":
        assert all(p.read_bytes() == content for p, content in config_before.items())
        assert load_settings(settings.root).universe == settings.universe
        request.job.result = {"status": "error", "message": "Report worker could not start"}
        assert "Retry report build" in request(path="/progress")[1]
        assert request("POST", "/report", {"token": token})[0] == 303
        assert request.location == "/progress"
        assert len(request.job.calls) == 2
        assert request.job.calls[0] == request.job.calls[1]

    active = load_settings(settings.root)
    monkeypatch.setattr(cli, "load_settings", lambda: active)
    monkeypatch.chdir(settings.root)
    steps = []
    names = [
        "command_config_check",
        "command_sec_health",
        "command_sec_filings_sync",
        "command_sec_facts_sync",
        "command_sec_financials_build",
        "_command_run_analysis",
        "command_provider_shadow_run",
        "command_validate",
    ]

    def handler(name):
        def run(args):
            steps.append(name)
            if name == "_command_run_analysis":
                run_analysis(active, demo=True)
            return 0

        return run

    for name in names:
        monkeypatch.setattr(cli, name, handler(name))
    payload = request.job.calls[0][1]
    request.job.result = universe_jobs.execute(
        {
            "root": str(settings.root),
            "kind": "report",
            "payload": payload,
        }
    )
    assert steps == names
    if launcher_first:
        assert request.server.claim_dashboard("launcher")
        status, html = request(path="/progress")
        assert status == 200 and "opened in another tab" in html
    else:
        assert request(path="/progress")[0] == 303
        assert request.location == "/dashboard"
        assert not request.server.claim_dashboard("launcher")
    assert request(path="/dashboard")[0] == 200
    data = json.loads(request(path="/api/report")[1])
    assert data["run"]["run_id"] == request.job.result["run_id"]
    assert {r["ticker"] for r in data["results"]} == {s.ticker for s in active.universe}


def test_review_cli_reopens_saved_proposal_without_discovery(settings, monkeypatch):
    from stockrank import universe_review_server

    folder = commands.directory(settings)
    folder.mkdir(parents=True)
    path = folder / "proposal-test.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(commands, "load_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(universe_review_server, "serve_review", lambda s, p, **kw: calls.append(p))
    assert commands.command_universe_review(Namespace(file=None, no_open=True)) == 0
    assert calls == [path]


def test_protected_excluded_stock_grows_list_and_survives_next_proposal(settings, evidence):
    ticker = settings.universe[0].ticker
    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    commands.approve_proposal(settings, proposal, "diversified", now=NOW, protect=[ticker])
    updated = load_settings(settings.root)
    assert len(updated.universe) == 51
    assert commands.load_policy(updated).keep_tickers == (ticker,)
    next_proposal = build_proposal(
        updated, commands.load_policy(updated), evidence, now=NOW, preview=True
    )
    selected = next_proposal["profiles"]["diversified"]
    assert ticker in selected["protected"] and ticker not in {
        r["ticker"] for r in selected["removals"]
    }
    assert "despite" in selected["override_warnings"][0]
    assert "Unavailable" in render_review(next_proposal, action_token="test")
    assert "Kept by override despite" in render_review(next_proposal, action_token="test")


def test_manual_inclusion_removal_and_invalid_edits(settings, evidence):
    from stockrank.universe_discovery import personalize_selection

    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    selected = proposal["profiles"]["best_overall"]
    skip = selected["members"][0]["ticker"]
    include = next(
        r["ticker"]
        for r in evidence["candidates"]
        if not r["exclusions"] and r["ticker"] not in selected["additions"]
    )
    result = personalize_selection(proposal, "best_overall", remove=[skip], include=[include])
    assert len(result["members"]) == 50
    assert include in result["additions"] and skip not in result["additions"]
    with pytest.raises(ValueError, match="pass discovery"):
        personalize_selection(proposal, "best_overall", include=[settings.universe[0].ticker])
    with pytest.raises(ValueError, match="pass discovery"):
        personalize_selection(proposal, "best_overall", include=["UNSCREENED"])
    with pytest.raises(ValueError, match="Uncheck Keep"):
        personalize_selection(
            proposal,
            "best_overall",
            protect=[settings.universe[0].ticker],
            remove=[settings.universe[0].ticker],
        )


def test_cap_blocks_activation_and_suggested_trim_requires_confirmation(settings, evidence):
    from stockrank.universe_discovery import (
        personalize_selection,
        suggested_trim,
        validate_selection,
    )

    proposal = build_proposal(settings, DiscoveryPolicy(), evidence, now=NOW, preview=True)
    protect = [s.ticker for s in settings.universe]
    include = [r["ticker"] for r in evidence["candidates"] if not r["exclusions"]]
    selected = personalize_selection(proposal, "diversified", protect=protect, include=include)
    assert len(selected["members"]) == 138
    with pytest.raises(ValueError, match="Remove 38"):
        commands.approve_proposal(
            settings, proposal, "diversified", now=NOW, protect=protect, include=include
        )
    assert not settings.local_preferences_path.exists()
    trim = suggested_trim(proposal, "diversified", selected)
    assert len(trim) == 38 and not set(trim) & set(protect)
    revised = personalize_selection(
        proposal, "diversified", protect=protect, include=include, remove=trim
    )
    validate_selection(revised)
    assert len(revised["members"]) == 100
    assert not settings.local_preferences_path.exists()
    commands.approve_proposal(
        settings, proposal, "diversified", now=NOW, protect=protect, include=include, remove=trim
    )
    assert len(load_settings(settings.root).universe) == 100


def test_candidate_preferences_validate_and_refresh_without_activation(
    settings, evidence, monkeypatch
):
    from stockrank import universe_jobs

    monkeypatch.chdir(settings.root)
    seen = []
    monkeypatch.setattr(
        commands, "collect_evidence", lambda settings, policy, **kw: seen.append(policy) or evidence
    )
    result = universe_jobs.execute(
        {
            "root": str(settings.root),
            "kind": "nominate",
            "payload": {"tickers": ["aapl", "MSFT", "aapl"]},
        }
    )
    assert result["status"] == "complete"
    assert seen[0].consider_tickers == ("AAPL", "MSFT")
    assert commands.load_policy(settings).consider_tickers == ("AAPL", "MSFT")
    assert not settings.local_preferences_path.exists()
    assert commands.schedule_state(settings) == {}
    with pytest.raises(ValueError):
        DiscoveryPolicy(consider_tickers=["../../file"])
    with pytest.raises(ValueError):
        DiscoveryPolicy(consider_tickers=[f"T{i}" for i in range(26)])


@pytest.mark.parametrize("launcher_first", [False, True])
def test_review_http_overrides_and_dashboard_handoff(
    settings, review_http, monkeypatch, launcher_first
):
    import re

    from stockrank import dashboard_server, universe_review_server

    request, proposal = review_http
    token = re.search(r'name="token" value="([^"]+)"', request()[1])[1]
    ticker = settings.universe[0].ticker
    removed = proposal["profiles"]["diversified"]["members"][0]["ticker"]
    status, html = request(
        "POST",
        "/confirm",
        {
            "token": token,
            "action": "approve",
            "profile": "diversified",
            "protect": [ticker],
            "remove": [removed],
        },
    )
    assert status == 200 and "50 / 100 stocks" in html
    nonce = re.search(r'name="confirmation" value="([^"]+)"', html)[1]
    assert request("POST", "/decision", {"token": token, "confirmation": nonce})[0] == 303
    assert request.location == "/progress"
    assert request.job.kind == "report"
    assert "Decision saved" in request(path="/progress")[1]
    assert "Approval is still required" not in request()[1]
    receipt = request.job.calls[-1][1]
    assert receipt["protected"] == [ticker] and receipt["manual_removals"] == [removed]
    data = {
        "run": {"status": "completed", "universe_name": receipt["version"], "run_id": "new-run"},
        "results": receipt["members"],
    }
    monkeypatch.setattr(universe_review_server, "load_dashboard", lambda root: data)
    monkeypatch.setattr(dashboard_server, "load_dashboard", lambda root: data)
    request.job.result = {"status": "complete", "run_id": "new-run"}
    # An idle browser preconnection and no /progress requests must not stall completion.
    import socket
    import time

    with socket.create_connection(("127.0.0.1", request.server.server_port), timeout=2):
        deadline = time.monotonic() + 4
        while not request.server.dashboard_ready and time.monotonic() < deadline:
            time.sleep(0.02)
        assert request.server.dashboard_ready
    if launcher_first:
        assert request.server.claim_dashboard("launcher")
        status, html = request(path="/progress")
        assert status == 200 and request.location is None
        assert "opened in another tab" in html
        assert 'http-equiv="refresh"' not in html
    else:
        assert request(path="/progress")[0] == 303 and request.location == "/dashboard"
        assert not request.server.claim_dashboard("launcher")
    assert request(path="/dashboard")[0] == 200
    assert json.loads(request(path="/api/report")[1])["run"]["run_id"] == "new-run"
    data["run"]["run_id"] = "different-run"
    assert request(path="/dashboard")[0] == 409


def test_review_reports_worker_failure_without_browser_polling(review_http, capsys):
    import time

    request, _ = review_http
    request.job.kind = "report"
    request.job.result = {"status": "error", "message": "Ranking validation failed"}
    deadline = time.monotonic() + 3
    while request.server.last_notice != ("error", "Ranking validation failed"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert not request.server.dashboard_ready
    assert "Ranking validation failed" in capsys.readouterr().out
    status, html = request(path="/progress")
    assert status == 200 and "Retry report build" in html
    assert "Ranking validation failed" in html


def test_review_http_cap_offers_manual_and_automatic_trim(settings, review_http):
    import re

    request, proposal = review_http
    token = re.search(r'name="token" value="([^"]+)"', request()[1])[1]
    fields = {
        "token": token,
        "action": "approve",
        "profile": "diversified",
        "protect": [s.ticker for s in settings.universe],
        "include": [r["ticker"] for r in proposal["evidence"]["candidates"] if not r["exclusions"]],
    }
    status, html = request("POST", "/confirm", fields)
    assert status == 200 and "Your list is full" in html
    assert "Make removals manually" in html and "Suggest a trim to 100" in html
    assert not settings.local_preferences_path.exists()
    status, html = request("POST", "/confirm", {**fields, "trim": "suggest"})
    assert status == 200 and "100 / 100 stocks" in html
    assert "Confirm &amp; build updated dashboard" in html
    assert not settings.local_preferences_path.exists()


def test_review_http_nomination_and_report_failure_are_explicit(settings, review_http):
    import re

    request, _ = review_http
    token = re.search(r'name="token" value="([^"]+)"', request()[1])[1]
    assert request("POST", "/nominate", {"token": token, "tickers": "bad/../ticker"})[0] == 409
    assert not request.job.calls
    assert request("POST", "/nominate", {"token": token, "tickers": "aapl, MSFT"})[0] == 303
    assert request.job.calls[-1] == ("nominate", {"tickers": ["AAPL", "MSFT"]})
    assert request.location == "/progress"
    request.job.kind = "report"
    request.job.result = {
        "status": "error",
        "message": "Approval is saved, but the report build failed.",
    }
    status, html = request(path="/progress")
    assert status == 200 and "Approval is saved" in html and "Retry report build" in html
    assert "http-equiv" not in html


def test_report_verification_rejects_old_incomplete_or_wrong_membership():
    from stockrank.universe_jobs import verify_report

    receipt = {"version": "approved", "members": [{"ticker": "AAPL"}]}
    data = {
        "run": {"run_id": "new", "status": "completed", "universe_name": "approved"},
        "results": [{"ticker": "AAPL"}],
    }
    assert verify_report(data, receipt, previous_run="old") == "new"
    with pytest.raises(ValueError):
        verify_report(data, receipt, previous_run="new")
    for broken in (
        {**data, "results": [{"ticker": "MSFT"}]},
        {**data, "run": {**data["run"], "status": "failed"}},
        {**data, "run": {**data["run"], "universe_name": "old"}},
    ):
        with pytest.raises(ValueError):
            verify_report(broken, receipt)


def test_handoff_accepts_actual_pipeline_dashboard_contract(settings):
    from stockrank.dashboard_data import load_dashboard
    from stockrank.pipeline import run_analysis
    from stockrank.universe_jobs import verify_report

    # Explicit synthetic pipeline in a temporary installation; no real data is replaced.
    run_id, _, _ = run_analysis(settings, demo=True)
    data = load_dashboard(settings.root)
    receipt = {
        "version": settings.raw["universe"]["name"],
        "members": [asdict(s) for s in settings.universe],
    }
    assert verify_report(data, receipt, previous_run="previous-run") == run_id


@pytest.mark.parametrize("validation_exit", [0, 1])
def test_sec_failure_does_not_block_validated_rankings_but_validation_does(
    settings, monkeypatch, validation_exit
):
    from stockrank import universe_jobs
    from stockrank.dashboard_data import load_dashboard
    from stockrank.pipeline import run_analysis

    monkeypatch.chdir(settings.root)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    for name in ("command_config_check", "command_sec_health", "command_sec_filings_sync"):
        monkeypatch.setattr(cli, name, lambda args: 0)

    def unavailable(args):
        print("WARNING: GEV: SEC duration fact is missing a start date.")
        return 1

    for name in (
        "command_sec_facts_sync",
        "command_sec_financials_build",
        "command_provider_shadow_run",
    ):
        monkeypatch.setattr(cli, name, unavailable)

    def ranking(args):
        run_analysis(settings, demo=True)
        return 0

    monkeypatch.setattr(cli, "_command_run_analysis", ranking)
    monkeypatch.setattr(cli, "command_validate", lambda args: validation_exit)
    receipt = {
        "version": settings.raw["universe"]["name"],
        "members": [asdict(s) for s in settings.universe],
    }
    request = {"root": str(settings.root), "kind": "report", "payload": receipt}
    if validation_exit:
        with pytest.raises(ValueError, match="Final validation"):
            universe_jobs.execute(request)
        assert not list((settings.runtime_dir / "universe").glob("report-status-*.json"))
    else:
        result = universe_jobs.execute(request)
        assert result["status"] == "complete"
        assert any("GEV" in w for w in result["warnings"])
        assert load_dashboard(settings.root)["workflow_warnings"] == result["warnings"]


def test_workflow_unknown_or_core_failure_still_blocks():
    from stockrank.universe_jobs import assess_workflow

    steps = [
        {"step": label, "exit_code": 0}
        for label in (
            "Configuration validation",
            "Yahoo ranking and base report",
            "Final validation",
        )
    ]
    with pytest.raises(ValueError):
        assess_workflow(1, steps)
    with pytest.raises(ValueError):
        assess_workflow(1, steps + [{"step": "Unknown new check", "exit_code": 1}])
    with pytest.raises(ValueError):
        assess_workflow(0, [])


def test_worker_output_reaches_terminal_and_log(settings, capsys):
    import io
    from types import SimpleNamespace

    from stockrank.universe_jobs import ReviewJob

    job = ReviewJob(settings)
    job.log = io.StringIO()
    job.process = SimpleNamespace(
        stdout=io.StringIO("[4/8] SEC data\nWARNING: GEV missing field\n")
    )
    job.relay_output()
    assert capsys.readouterr().out == job.log.getvalue()
    assert "GEV missing field" in job.log.getvalue()


@pytest.mark.parametrize("open_browser", [True, False])
@pytest.mark.parametrize("review_claimed", [True, False])
def test_review_reuses_normal_terminal_animation_after_dashboard_opens(
    settings, monkeypatch, open_browser, review_claimed
):
    from stockrank import daily_workflow, universe_review_server

    calls = []

    class Server:
        server_port = 12345
        dashboard_open = False
        dashboard_ready = False
        dashboard_handoff = "review" if review_claimed else None

        def claim_dashboard(self, owner):
            if self.dashboard_handoff is None:
                self.dashboard_handoff = owner
            return self.dashboard_handoff == owner

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("close server and worker")

        def serve_forever(self):
            self.dashboard_ready = True

        def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setattr(universe_review_server, "make_review_server", lambda *args, **kw: Server())
    opened = []
    monkeypatch.setattr(universe_review_server.webbrowser, "open", opened.append)

    def welcome(lifetime):
        calls.append("existing welcome animation")
        raise KeyboardInterrupt

    monkeypatch.setattr(daily_workflow, "wait_with_welcome", welcome)
    monkeypatch.setattr(daily_workflow, "print_goodbye", lambda: calls.append("existing farewell"))
    universe_review_server.serve_review(settings, None, open_browser=open_browser)
    expected = ["http://127.0.0.1:12345/"] if open_browser else []
    if open_browser and not review_claimed:
        expected.append("http://127.0.0.1:12345/dashboard")
    assert opened == expected
    assert calls == [
        "existing welcome animation",
        "shutdown",
        "close server and worker",
        "existing farewell",
    ]


@pytest.mark.parametrize("force", [False, True])
def test_morning_opens_due_review_instead_of_old_dashboard(settings, monkeypatch, force):
    from stockrank import universe_review_server

    calls = []
    path = settings.root / "weekly.json"
    monkeypatch.setattr(
        cli, "command_daily_report", lambda args: pytest.fail("Report must wait for a decision")
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(commands, "run_due_discovery", lambda: path)
    monkeypatch.setattr(
        universe_review_server, "serve_review", lambda s, p, **kw: calls.append(("review", p, kw))
    )
    monkeypatch.setattr(
        cli, "command_dashboard", lambda args: pytest.fail("Must show review first")
    )
    assert cli.command_morning(Namespace(force=force)) == 0
    assert calls == [("review", path, {"report_force": force})]


def test_morning_sec_warning_still_reaches_discovery(settings, monkeypatch):
    from stockrank import dashboard_data

    calls = []

    def daily(args):
        args.step_results.extend(
            {"step": name, "exit_code": 0}
            for name in (
                "Configuration validation",
                "Yahoo ranking and base report",
                "Final validation",
            )
        )
        args.step_results.append({"step": "SEC Company Facts sync", "exit_code": 1})
        print("WARNING: GEV missing a start date")
        return 1

    monkeypatch.setattr(cli, "command_daily_report", daily)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        dashboard_data,
        "load_dashboard",
        lambda root: {
            "run": {
                "run_id": "morning-test",
                "status": "completed",
                "universe_name": settings.raw["universe"]["name"],
            },
            "results": [asdict(s) for s in settings.universe],
        },
    )
    monkeypatch.setattr(commands, "run_due_discovery", lambda: calls.append("discovery"))
    monkeypatch.setattr(cli, "command_dashboard", lambda args: calls.append("dashboard") or 0)
    assert cli.command_morning(Namespace(force=False)) == 0
    assert calls == ["discovery", "dashboard"]
    status = json.loads(
        (settings.runtime_dir / "universe/report-status-morning-test.json").read_text()
    )
    assert any("GEV" in w for w in status["warnings"])
