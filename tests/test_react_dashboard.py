from __future__ import annotations

import hashlib
import http.client
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer

import pytest
from test_dashboard_execution import _isolated_project, _save_financial_snapshot, _save_run

from stockrank import dashboard_server
from stockrank.config import load_settings
from stockrank.dashboard_data import json_value, load_dashboard
from stockrank.models import ProviderComparisonRun
from stockrank.storage import Storage


def test_read_model_has_no_write_or_private_config_side_effects(tmp_path):
    root = _isolated_project(tmp_path)
    assert load_dashboard(root)["run"] is None
    assert not load_settings(root).database_path.exists()
    _save_run(root, completed_at=datetime(2026, 8, 31, 12, 1, tzinfo=UTC), eligible=True)
    database = load_settings(root).database_path
    before = hashlib.sha256(database.read_bytes()).digest()
    data = load_dashboard(root)
    assert hashlib.sha256(database.read_bytes()).digest() == before
    assert data["run"]["run_id"] == "dashboard-run"
    assert "config_json" not in data["run"]
    assert data["candidates"][0]["ticker"] == "AAPL"
    assert data["candidates"][0]["score_tier"] == "Top Tier"
    assert "score ≥ 55" in data["policy"]
    assert str(root) not in json.dumps(data)


@pytest.mark.parametrize("completed", [None, datetime(2026, 8, 31, 12, 1)])  # noqa: DTZ001 - legacy fixture
def test_read_model_withholds_unbounded_legacy_evidence(tmp_path, completed):
    root = _isolated_project(tmp_path)
    _save_run(root, completed_at=completed, eligible=True, full_policy=False)
    data = load_dashboard(root)
    assert not data["financial_cutoff_valid"]
    assert data["financials"] == []
    assert data["filings"]["AAPL"]["items"] == []
    assert data["filings"]["AAPL"]["limitation"]


def test_read_model_keeps_report_cutoffs_membership_and_shadow_binding(tmp_path):
    root = _isolated_project(tmp_path)
    cutoff = datetime(2026, 8, 31, 12, 1, tzinfo=UTC)
    _save_run(root, completed_at=cutoff, eligible=True)
    storage = Storage(load_settings(root).database_path)
    for ticker, name, built in [
        ("AAPL", "report", cutoff - timedelta(minutes=1)),
        ("AAPL", "future", cutoff + timedelta(days=1)),
        ("MSFT", "outside-universe", cutoff - timedelta(minutes=1)),
    ]:
        _save_financial_snapshot(
            storage,
            snapshot_id=name,
            ticker=ticker,
            as_of=cutoff - timedelta(minutes=2),
            built_at=built,
            formula_version=name,
        )
    for name, run_id, delta in [("linked", "dashboard-run", 1), ("current", "other", 2)]:
        storage.save_provider_comparison_run(
            ProviderComparisonRun(
                comparison_run_id=name,
                started_at=cutoff,
                completed_at=cutoff + timedelta(days=delta),
                as_of=cutoff,
                config_version="policy",
                universe_name="test-universe",
                scope_count=1,
                universe_size=1,
                full_universe=True,
                status="complete",
                warnings=(),
                analysis_run_id=run_id,
            ),
            (),
        )
    data = load_dashboard(root)
    assert [r["formula_version"] for r in data["financials"]] == ["report"]
    assert data["report_shadow"]["comparison_run_id"] == "linked"


def test_read_model_preserves_empty_candidate_rules_and_missing_values(tmp_path):
    root = _isolated_project(tmp_path)
    _save_run(root, completed_at=None, eligible=False)
    data = load_dashboard(root)
    assert not data["candidates"]
    assert "coverage ≥ 60%" in data["empty_candidates"]
    assert "$1,000,000" in data["empty_candidates"]
    assert json_value([float("nan"), float("inf"), None, 0]) == [None, None, None, 0]


@pytest.fixture
def local_server(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard",
        lambda root: {"run": {"run_id": "displayed"}, "results": []},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.make_handler(tmp_path))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)


def request(server, path, *, headers=None, method="GET"):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_server_serves_bundled_assets_and_read_only_report(local_server):
    status, headers, body = request(local_server, "/api/report")
    assert status == 200
    assert json.loads(body)["run"]["run_id"] == "displayed"
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for path in ["/", "/app.js", "/app.css"]:
        assert request(local_server, path)[0] == 200
    assert request(local_server, "/api/report", method="POST")[0] == 501


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example"},
        {"Origin": "https://attacker.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_server_rejects_cross_origin_and_rebinding(local_server, headers):
    assert request(local_server, "/api/report", headers=headers)[0] == 403


@pytest.mark.parametrize(
    "path", ["/.env", "/../config/preferences.local.toml", "/runtime/research.db"]
)
def test_server_never_serves_arbitrary_project_files(local_server, path):
    assert request(local_server, path)[0] == 404


def test_csv_rejects_report_switch_and_uses_bom(local_server):
    assert request(local_server, "/api/rankings.csv?run=stale")[0] == 409
    status, headers, body = request(local_server, "/api/rankings.csv?run=displayed")
    assert status == 200
    assert body.startswith(b"\xef\xbb\xbf")
    assert "attachment" in headers["Content-Disposition"]


def test_server_error_does_not_expose_internal_paths(local_server, monkeypatch):
    def broken(root):
        raise ValueError("private-secret-path")

    monkeypatch.setattr(dashboard_server, "load_dashboard", broken)
    status, _, body = request(local_server, "/api/report")
    assert status == 500
    assert b"private-secret-path" not in body
