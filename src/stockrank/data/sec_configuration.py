"""Validation and loading of SEC configuration and concept mappings."""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from stockrank.data.sec_transport import SEC_ALLOWED_HOSTS, validate_sec_user_agent
from stockrank.data.sec_types import SecConceptSpec, SecConfigurationError

if TYPE_CHECKING:
    from stockrank.config import Settings


XBRL_MEMBER_PATTERN = re.compile(
    r"^(?P<taxonomy>[a-z][a-z0-9-]*):(?P<concept>[A-Za-z_][A-Za-z0-9_.-]*)$"
)


def normalize_sec_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


def validate_sec_configuration(settings: Settings) -> list[str]:
    """Validate all local SEC settings without provider or network access."""
    errors: list[str] = []
    config = settings.raw.get("sec", {})
    if not isinstance(config, dict):
        return ["sec must be a configuration table"]

    try:
        validate_sec_user_agent(settings.sec_user_agent)
    except SecConfigurationError as exc:
        errors.append(str(exc))

    identity_url = str(config.get("identity_url", "")).strip()
    parsed_identity_url = urlparse(identity_url)
    if (
        parsed_identity_url.scheme != "https"
        or parsed_identity_url.hostname not in SEC_ALLOWED_HOSTS
    ):
        errors.append("sec.identity_url must use HTTPS on an approved sec.gov host")

    def numeric(name: str, *, allow_zero: bool = False, maximum: float | None = None) -> None:
        try:
            value = float(config[name])
        except (KeyError, TypeError, ValueError):
            errors.append(f"sec.{name} must be a valid number")
            return
        invalid_minimum = value < 0 if allow_zero else value <= 0
        if not math.isfinite(value) or invalid_minimum or (maximum is not None and value > maximum):
            requirement = "nonnegative" if allow_zero else "positive"
            if maximum is not None:
                requirement += f" and no more than {maximum:g}"
            errors.append(f"sec.{name} must be {requirement}")

    def integer(name: str, *, minimum: int) -> None:
        value = config.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            errors.append(f"sec.{name} must be an integer of at least {minimum}")

    numeric("requests_per_second", maximum=10)
    numeric("request_timeout_seconds")
    integer("request_retries", minimum=0)
    numeric("retry_backoff_seconds", allow_zero=True)
    numeric("identity_cache_ttl_hours")
    numeric("submissions_cache_ttl_hours")
    integer("filing_history_years", minimum=1)
    numeric("companyfacts_cache_ttl_hours")
    numeric("companyfacts_full_refresh_hours")
    numeric("companyfacts_recent_filing_window_hours", allow_zero=True)
    numeric("companyfacts_recent_filing_retry_hours")
    integer("companyfacts_history_years", minimum=1)
    numeric("maximum_stale_cache_hours")

    for name in ("filing_forms", "companyfacts_core_concepts"):
        values = config.get(name)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            errors.append(f"sec.{name} must be a nonempty array of nonempty strings")

    if not isinstance(config.get("allow_stale_cache_on_error"), bool):
        errors.append("sec.allow_stale_cache_on_error must be true or false")
    for name in ("entity_overrides_path", "companyfacts_concepts_path"):
        if not str(config.get(name, "")).strip():
            errors.append(f"sec.{name} must not be empty")

    overrides_path = Path(str(config.get("entity_overrides_path", "")))
    if not overrides_path.is_absolute():
        overrides_path = settings.root / overrides_path
    if not overrides_path.is_file():
        errors.append(f"SEC entity overrides file not found: {overrides_path}")
    else:
        try:
            load_sec_entity_overrides(settings)
        except SecConfigurationError as exc:
            errors.append(str(exc))
    try:
        load_sec_concept_specs(settings)
    except SecConfigurationError as exc:
        errors.append(str(exc))
    return errors


def load_sec_entity_overrides(settings: Settings) -> dict[str, tuple[str, ...]]:
    configured_path = settings.raw.get("sec", {}).get(
        "entity_overrides_path", "config/sec_entity_overrides.toml"
    )
    path = Path(str(configured_path))
    if not path.is_absolute():
        path = settings.root / path
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SecConfigurationError(
            f"SEC entity overrides could not be parsed: {path}: {exc}"
        ) from exc
    except OSError as exc:
        raise SecConfigurationError(
            f"SEC entity overrides could not be read: {path}: {exc}"
        ) from exc
    ticker_records = payload.get("tickers", {})
    if not isinstance(ticker_records, dict):
        raise SecConfigurationError("SEC entity overrides must contain a tickers table.")
    output: dict[str, tuple[str, ...]] = {}
    for ticker, record in ticker_records.items():
        if not isinstance(record, dict):
            raise SecConfigurationError(f"SEC entity override for {ticker} must be a table.")
        values = record.get("additional_ciks", [])
        if not isinstance(values, list):
            raise SecConfigurationError(
                f"SEC entity override additional_ciks for {ticker} must be an array."
            )
        reason = str(record.get("reason", "")).strip()
        evidence_url = str(record.get("evidence_url", "")).strip()
        evidence = urlparse(evidence_url)
        if not reason:
            raise SecConfigurationError(f"SEC entity override for {ticker} requires a reason.")
        if evidence.scheme != "https" or evidence.hostname not in SEC_ALLOWED_HOSTS:
            raise SecConfigurationError(
                f"SEC entity override for {ticker} requires an official SEC evidence URL."
            )
        ciks: list[str] = []
        for raw_cik in values:
            cik = str(raw_cik).strip()
            if not cik.isdigit() or len(cik) > 10:
                raise SecConfigurationError(
                    f"SEC entity override for {ticker} contains invalid CIK: {cik}"
                )
            ciks.append(cik.zfill(10))
        output[normalize_sec_ticker(str(ticker))] = tuple(dict.fromkeys(ciks))
    return output


def load_sec_concept_specs(settings: Settings) -> tuple[SecConceptSpec, ...]:
    configured_path = settings.raw.get("sec", {}).get(
        "companyfacts_concepts_path", "config/sec_companyfacts.toml"
    )
    path = Path(str(configured_path))
    if not path.is_absolute():
        path = settings.root / path
    if not path.exists():
        raise SecConfigurationError(f"SEC Company Facts concept map not found: {path}")
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SecConfigurationError(
            f"SEC Company Facts concept map could not be parsed: {path}: {exc}"
        ) from exc
    except OSError as exc:
        raise SecConfigurationError(
            f"SEC Company Facts concept map could not be read: {path}: {exc}"
        ) from exc
    concepts = payload.get("concepts")
    if not isinstance(concepts, dict) or not concepts:
        raise SecConfigurationError("SEC Company Facts concept map must define concepts.")

    output: list[SecConceptSpec] = []
    for canonical_name, record in concepts.items():
        if not isinstance(record, dict):
            raise SecConfigurationError(
                f"SEC Company Facts concept {canonical_name} must be a table."
            )
        period_type = str(record.get("period_type", "")).strip().lower()
        if period_type not in {"instant", "duration"}:
            raise SecConfigurationError(
                f"SEC Company Facts concept {canonical_name} has invalid period_type."
            )
        units = record.get("units")
        members = record.get("members")
        if (
            not isinstance(units, list)
            or not units
            or not all(isinstance(unit, str) and unit.strip() for unit in units)
        ):
            raise SecConfigurationError(
                f"SEC Company Facts concept {canonical_name} requires units."
            )
        if not isinstance(members, list) or not members:
            raise SecConfigurationError(
                f"SEC Company Facts concept {canonical_name} requires members."
            )
        parsed_members: list[tuple[str, str]] = []
        for member in members:
            match = XBRL_MEMBER_PATTERN.fullmatch(str(member).strip())
            if not match:
                raise SecConfigurationError(
                    f"SEC Company Facts concept {canonical_name} has invalid member: {member}"
                )
            parsed_members.append((match["taxonomy"], match["concept"]))
        output.append(
            SecConceptSpec(
                canonical_name=str(canonical_name).strip(),
                period_type=period_type,
                units=tuple(dict.fromkeys(str(unit).strip() for unit in units)),
                members=tuple(dict.fromkeys(parsed_members)),
            )
        )
    return tuple(output)
