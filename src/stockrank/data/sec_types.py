"""Shared SEC records and provider errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stockrank.models import SecCompanyFact, SecFiling


class SecError(RuntimeError):
    """Base class for SEC provider failures."""


class SecConfigurationError(SecError):
    """Raised when the SEC client is configured unsafely."""


class SecRequestError(SecError):
    """Raised when an SEC request cannot be completed."""


class SecPayloadError(SecError):
    """Raised when an SEC response does not match the documented JSON shape."""


@dataclass(frozen=True)
class SecJsonDocument:
    payload: Any
    source_url: str
    fetched_at: datetime
    cache_hit: bool
    stale: bool = False


@dataclass(frozen=True)
class SecCompanyIdentity:
    cik: str
    name: str
    ticker: str
    exchange: str


@dataclass(frozen=True)
class SecIdentitySnapshot:
    identities: tuple[SecCompanyIdentity, ...]
    source_url: str
    fetched_at: datetime
    cache_hit: bool
    stale: bool


@dataclass(frozen=True)
class SecSubmissionSnapshot:
    cik: str
    ticker: str
    company_name: str
    filings: tuple[SecFiling, ...]
    source_urls: tuple[str, ...]
    fetched_at: datetime
    cache_hits: int
    documents_checked: int
    stale: bool


@dataclass(frozen=True)
class SecConceptSpec:
    canonical_name: str
    period_type: str
    units: tuple[str, ...]
    members: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SecCompanyFactsSnapshot:
    cik: str
    ticker: str
    company_name: str
    facts: tuple[SecCompanyFact, ...]
    source_url: str
    fetched_at: datetime
    cache_hit: bool
    stale: bool
    configured_concepts: tuple[str, ...]
    present_concepts: tuple[str, ...]
    unmatched_accessions: int
