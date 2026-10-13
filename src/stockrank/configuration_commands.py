"""Installation checks and this computer's profile/universe configuration."""

from __future__ import annotations

import argparse
import copy
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stockrank.config import TICKER_PATTERN, Settings, load_settings, validate_settings
from stockrank.customization import (
    HORIZONS,
    PROFILE_NAMES,
    RISK_LEVELS,
    PersonalizationUpdateError,
    enrich_universe,
    model_identifier,
    parse_component_weights,
    parse_tickers,
    profile_weights,
    read_universe_input,
    reset_local_customization,
    save_local_customization,
    universe_identifier,
)
from stockrank.dashboard_server import ASSET_REPAIR, missing_dashboard_assets
from stockrank.data import YFinanceProvider
from stockrank.data.sec import validate_sec_configuration
from stockrank.models import Security
from stockrank.sec_filings_commands import command_sec_health
from stockrank.storage import Storage


def command_setup_check(_: argparse.Namespace) -> int:
    """Verify that a cloned checkout can initialize its local runtime safely."""
    failures: list[str] = []
    try:
        settings = load_settings()
    except (OSError, KeyError, ValueError) as exc:
        print(f"ERROR: configuration could not be loaded: {exc}", file=sys.stderr)
        return 1

    required_paths = (
        settings.root / "config" / "preferences.toml",
        settings.root / str(settings.raw["universe"]["path"]),
    )
    for path in required_paths:
        if not path.is_file():
            failures.append(f"Required project file is missing: {path}")
    config_errors, config_warnings = validate_settings(settings)
    failures.extend(config_errors)

    if missing := missing_dashboard_assets():
        failures.append(f"{ASSET_REPAIR} Missing: {', '.join(missing)}")

    failures.extend(validate_sec_configuration(settings))

    try:
        storage = Storage(settings.database_path)
        storage.initialize()
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=settings.runtime_dir, prefix="setup-check-"):
            pass
    except OSError as exc:
        failures.append(f"Runtime directory is not writable: {exc}")

    print(
        f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} | "
        f"project: {settings.root}"
    )
    print(
        f"Universe: {len(settings.universe)} securities | model: {settings.model_version} | "
        f"profile: {settings.profile_name} | runtime: {settings.runtime_dir}"
    )
    for warning in config_warnings:
        print(f"WARNING: {warning}")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        print("Setup check: NOT READY")
        return 1
    print("Setup check: READY")
    return 0


def command_config_check(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except (OSError, KeyError, ValueError) as exc:
        print(f"Configuration check: INVALID | {exc}", file=sys.stderr)
        return 1
    errors, warnings = validate_settings(settings)
    errors.extend(validate_sec_configuration(settings))
    print(
        f"Configuration: {'local profile' if settings.uses_local_preferences else 'project default'} | "
        f"profile={settings.profile_name} | horizon={settings.investment_horizon} | "
        f"risk={settings.risk_tolerance}"
    )
    print(
        f"Universe={settings.raw['universe']['name']} ({len(settings.universe)} stocks) | "
        f"model={settings.model_version}"
    )
    print(
        "Component weights: "
        + ", ".join(
            f"{component}={weight:.1%}" for component, weight in settings.component_weights.items()
        )
    )
    validity = settings.raw["scoring"]["validity"]
    print(
        f"Scoring validity: minimum peers={validity['minimum_metric_peer_count']} | "
        f"debt/equity minimum={float(validity['minimum_debt_to_equity']):g} | "
        f"ROE maximum={float(validity['maximum_return_on_equity']):.0%}"
    )
    eligibility = settings.raw["scoring"]["eligibility"]
    print(
        "Candidate liquidity: "
        f"minimum price={float(eligibility['minimum_latest_price']):.2f} | "
        "minimum 20-day average dollar volume="
        f"{float(eligibility['minimum_average_dollar_volume_20d']):,.0f}"
    )
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print("Configuration check: INVALID")
        return 1
    if not bool(getattr(args, "live", False)):
        print("Configuration check: VALID (local checks only)")
        print("Use `stockrank config-check --live` for Yahoo price and SEC identity coverage.")
        return 0

    provider = YFinanceProvider(
        retries=int(settings.raw["provider"]["request_retries"]),
        backoff_seconds=float(settings.raw["provider"]["retry_backoff_seconds"]),
    )
    today = datetime.now(UTC).date()
    try:
        price_data, price_warnings = provider.fetch_prices(
            list(settings.universe), today - timedelta(days=14), today + timedelta(days=1)
        )
    except Exception as exc:  # noqa: BLE001 - provider exceptions vary.
        print(f"ERROR: Yahoo price validation failed: {exc}", file=sys.stderr)
        return 1
    covered = sum(bool(price_data.get(security.ticker)) for security in settings.universe)
    print(f"Yahoo price coverage: {covered}/{len(settings.universe)}")
    for warning in price_warnings:
        print(f"WARNING: {warning}")
    sec_result = command_sec_health(argparse.Namespace(force=False))
    if covered != len(settings.universe) or sec_result:
        print("Configuration check: PROVIDER ATTENTION REQUIRED")
        return 1
    print("Configuration check: VALID, including live provider coverage")
    return 0


def _prompt(label: str, current: str) -> str:
    value = input(f"{label} [{current}]: ").strip()
    return value or current


def _prompt_choice(label: str, current: str, choices: tuple[str, ...]) -> str:
    while True:
        value = _prompt(f"{label} ({'/'.join(choices)})", current).lower()
        if value in choices:
            return value
        print("Please choose one of: " + ", ".join(choices))


def command_configure(args: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    if bool(args.reset):
        try:
            backups = reset_local_customization(root)
        except PersonalizationUpdateError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if backups:
            print("Default configuration restored. Backups:")
            for backup in backups:
                print(f"  {backup}")
        else:
            print("Default configuration was already active; no local files changed.")
        return command_config_check(argparse.Namespace(live=False))

    try:
        current = load_settings(root)
    except (OSError, KeyError, ValueError) as exc:
        print(f"WARNING: current local configuration is invalid ({exc}); using project defaults")
        current = load_settings(root, root / "config" / "preferences.toml")
    interactive = not bool(args.yes)
    profile = args.profile or (
        _prompt_choice("Ranking profile", current.profile_name, PROFILE_NAMES)
        if interactive
        else current.profile_name
    )
    horizon = args.horizon or (
        _prompt_choice("Investment horizon", current.investment_horizon, HORIZONS)
        if interactive
        else current.investment_horizon
    )
    risk = args.risk or (
        _prompt_choice("Risk tolerance", current.risk_tolerance, RISK_LEVELS)
        if interactive
        else current.risk_tolerance
    )
    try:
        weights = (
            parse_component_weights(args.weights)
            if args.weights
            else profile_weights(profile, risk, horizon)
        )
        weights = {component: round(weight, 12) for component, weight in weights.items()}
        candidate_limit = int(
            args.candidate_limit
            if args.candidate_limit is not None
            else _prompt("Top-candidate limit", str(current.raw["app"]["top_candidate_limit"]))
            if interactive
            else current.raw["app"]["top_candidate_limit"]
        )
        minimum_score = float(
            args.minimum_score
            if args.minimum_score is not None
            else _prompt(
                "Minimum candidate score", str(current.raw["app"]["minimum_candidate_score"])
            )
            if interactive
            else current.raw["app"]["minimum_candidate_score"]
        )
        minimum_coverage = float(
            args.minimum_coverage
            if args.minimum_coverage is not None
            else _prompt(
                "Minimum data coverage (0-1)",
                str(current.raw["app"]["minimum_overall_coverage"]),
            )
            if interactive
            else current.raw["app"]["minimum_overall_coverage"]
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    requested_universe = None
    use_default_universe = bool(getattr(args, "use_default_universe", False))
    if args.tickers:
        requested_universe = [Security(ticker, "", "") for ticker in parse_tickers(args.tickers)]
    elif args.universe_file:
        try:
            requested_universe = read_universe_input(Path(args.universe_file))
        except (OSError, ValueError) as exc:
            print(f"ERROR: could not read universe file: {exc}", file=sys.stderr)
            return 2
    elif interactive and input("Customize the stock universe? [y/N]: ").strip().lower() == "y":
        requested_universe = [
            Security(ticker, "", "")
            for ticker in parse_tickers(input("Enter comma-separated tickers: "))
        ]

    securities = list(current.universe)
    universe_path = str(current.raw["universe"]["path"])
    universe_name = str(current.raw["universe"]["name"])
    if use_default_universe:
        defaults = load_settings(root, root / "config" / "preferences.toml")
        securities = list(defaults.universe)
        universe_path = str(defaults.raw["universe"]["path"])
        universe_name = str(defaults.raw["universe"]["name"])
    elif requested_universe is not None:
        if not requested_universe:
            print("ERROR: custom universe is empty", file=sys.stderr)
            return 2
        invalid_tickers = [
            security.ticker
            for security in requested_universe
            if not TICKER_PATTERN.fullmatch(security.ticker)
        ]
        if invalid_tickers:
            print(
                "ERROR: invalid ticker format: " + ", ".join(invalid_tickers),
                file=sys.stderr,
            )
            return 2
        print(f"Validating metadata for {len(requested_universe)} requested ticker(s)...")
        securities, enrichment_warnings = enrich_universe(requested_universe)
        for warning in enrichment_warnings:
            print(f"WARNING: {warning}")
        universe_path = "config/universe.local.csv"
        universe_name = universe_identifier(securities)

    scoring = copy.deepcopy(current.raw["scoring"])
    model_version = model_identifier(profile, scoring, weights)
    candidate_raw = copy.deepcopy(current.raw)
    candidate_raw["app"].update(
        {
            "top_candidate_limit": candidate_limit,
            "minimum_candidate_score": minimum_score,
            "minimum_overall_coverage": minimum_coverage,
        }
    )
    candidate_raw["preferences"] = {
        "profile": profile,
        "investment_horizon": horizon,
        "risk_tolerance": risk,
    }
    candidate_raw["universe"].update({"name": universe_name, "path": universe_path})
    candidate_raw["scoring"]["model_version"] = model_version
    candidate_raw["scoring"]["overall"] = weights
    candidate = Settings(root=root, raw=candidate_raw, universe=tuple(securities))
    errors, warnings = validate_settings(candidate)
    print("\nProposed personal configuration")
    print(f"  Profile: {profile} | horizon: {horizon} | risk: {risk}")
    print(f"  Model: {model_version}")
    print(f"  Universe: {universe_name} ({len(securities)} stocks)")
    print("  Weights: " + ", ".join(f"{key}={value:.1%}" for key, value in weights.items()))
    print(
        f"  Candidates: top {candidate_limit}, score >= {minimum_score:g}, "
        f"coverage >= {minimum_coverage:.0%}"
    )
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print("No files were changed.")
        return 2
    if interactive and input("Save this configuration? [y/N]: ").strip().lower() != "y":
        print("Cancelled; no files were changed.")
        return 0
    try:
        path, _backups = save_local_customization(
            root,
            securities=(
                securities if requested_universe is not None and not use_default_universe else None
            ),
            profile=profile,
            horizon=horizon,
            risk=risk,
            weights=weights,
            model_version=model_version,
            universe_name=universe_name,
            universe_path=universe_path,
            candidate_limit=candidate_limit,
            minimum_score=minimum_score,
            minimum_coverage=minimum_coverage,
        )
    except PersonalizationUpdateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Saved personal settings: {path}")
    print("Run `stockrank config-check --live` before the first report for this universe.")
    return command_config_check(argparse.Namespace(live=False))
