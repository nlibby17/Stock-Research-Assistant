"""Exercise the static boundary with both valid and deliberately broken callers."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMPORTS = """
from typing import assert_type
from stockrank.config import Settings
from stockrank.scoring_inputs import ScoringInput
from stockrank.scoring import metric_peer_counts, score_universe
from stockrank.universe_sources import DiscoveryEvidence, rescore_evidence
"""


def check_source(tmp_path, source):
    path = tmp_path / "contract_example.py"
    path.write_text(IMPORTS + source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(ROOT / "pyproject.toml"), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def test_optional_metadata_nullable_metrics_and_evidence_reuse_are_typed(tmp_path):
    result = check_source(
        tmp_path,
        """
minimal: ScoringInput = {"metrics": {}}
complete: ScoringInput = {
    "metrics": {"revenue_growth": None, "latest_price": 10.0},
    "company": None, "sector": None, "price_as_of": None, "warnings": [],
}
def consume(settings: Settings, evidence: DiscoveryEvidence) -> None:
    inputs: dict[str, ScoringInput] = {"A": minimal, "B": complete}
    score_universe(settings, inputs)
    metric_peer_counts(settings, inputs)
    assert_type(evidence["verified_inputs"], dict[str, ScoringInput])
    assert_type(rescore_evidence(settings, evidence), DiscoveryEvidence)
    score_universe(settings, evidence["verified_inputs"])
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source,error",
    [
        ('entry: ScoringInput = {}', 'Missing key "metrics"'),
        ('entry: ScoringInput = {"metric": {}}', 'Extra key "metric"'),
        ('entry: ScoringInput = {"metrics": {"latest_price": "10"}}', '[dict-item]'),
        ('entry: ScoringInput = {"metrics": None}', '[typeddict-item]'),
        ('entry: ScoringInput = {"metrics": {}, "warnings": None}', '[typeddict-item]'),
        ('entry: ScoringInput = {"metrics": {}, "company": 42}', '[typeddict-item]'),
        ('entry: ScoringInput = {"metrics": {}, "price_as_of": 20261013}', '[typeddict-item]'),
        (
            (
                'def broken(settings: Settings) -> None:\n'
                '    score_universe(settings, {"A": {"warnings": []}})'
            ),
            'Missing key "metrics"',
        ),
        (
            (
                'def broken(settings: Settings) -> None:\n'
                '    metric_peer_counts(settings, {"A": {"metrics": {"beta": "bad"}}})'
            ),
            '[dict-item]',
        ),
        (
            (
                'def broken(evidence: DiscoveryEvidence) -> None:\n'
                '    evidence["verified_inputs"]["A"] = {"metrics": {"beta": "bad"}}'
            ),
            '[dict-item]',
        ),
    ],
)
def test_static_check_rejects_invalid_scoring_inputs(tmp_path, source, error):
    result = check_source(tmp_path, source)
    assert result.returncode == 1, result.stdout + result.stderr
    assert error in result.stdout, result.stdout + result.stderr
