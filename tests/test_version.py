import tomllib
from pathlib import Path

from stockrank import __version__
from stockrank.version import APP_VERSION


def test_application_version_matches_package_metadata():
    with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert __version__ == APP_VERSION == project["version"]
