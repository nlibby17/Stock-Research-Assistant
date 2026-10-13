"""SEC HTTP access, throttling, retries, and disk caching."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from stockrank.data.sec_types import (
    SecConfigurationError,
    SecJsonDocument,
    SecPayloadError,
    SecRequestError,
)

if TYPE_CHECKING:
    from stockrank.config import Settings


SEC_ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
SEC_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
SEC_CACHE_SCHEMA_VERSION = 1
SEC_CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)
EMAIL_PATTERN = re.compile(r"(?<!\S)[^@\s]+@[^@\s]+\.[^@\s]+(?!\S)")


def validate_sec_user_agent(value: str) -> str:
    normalized = " ".join(value.split())
    lowered = normalized.lower()
    if not normalized:
        raise SecConfigurationError(
            "SEC_USER_AGENT is missing. Add an application name and contact email to .env."
        )
    if "your-email" in lowered or "example.com" in lowered:
        raise SecConfigurationError(
            "SEC_USER_AGENT still contains a placeholder; configure a real contact email."
        )
    if not EMAIL_PATTERN.search(normalized):
        raise SecConfigurationError(
            "SEC_USER_AGENT must contain an application name and contact email."
        )
    if len(normalized) < 12:
        raise SecConfigurationError("SEC_USER_AGENT is too short to identify the application.")
    return normalized


class SecClient:
    """Small SEC JSON client with declared identity, throttling, retries, and disk cache."""

    def __init__(
        self,
        *,
        user_agent: str,
        cache_dir: Path,
        requests_per_second: float = 5.0,
        timeout_seconds: float = 20.0,
        retries: int = 2,
        backoff_seconds: float = 1.0,
        allow_stale_on_error: bool = True,
        maximum_stale_hours: float = 168.0,
        session: Any | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.user_agent = validate_sec_user_agent(user_agent)
        if not 0 < requests_per_second <= 10:
            raise SecConfigurationError(
                "SEC requests_per_second must be greater than zero and no more than 10."
            )
        if timeout_seconds <= 0:
            raise SecConfigurationError("SEC request_timeout_seconds must be greater than zero.")
        if retries < 0:
            raise SecConfigurationError("SEC request_retries cannot be negative.")
        if backoff_seconds < 0:
            raise SecConfigurationError("SEC retry_backoff_seconds cannot be negative.")
        if maximum_stale_hours <= 0:
            raise SecConfigurationError("SEC maximum_stale_cache_hours must be positive.")
        self.cache_dir = cache_dir
        self.requests_per_second = float(requests_per_second)
        self.timeout_seconds = float(timeout_seconds)
        self.retries = int(retries)
        self.backoff_seconds = float(backoff_seconds)
        self.allow_stale_on_error = bool(allow_stale_on_error)
        self.maximum_stale_hours = float(maximum_stale_hours)
        self.session = session or requests.Session()
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self._request_lock = threading.Lock()
        self._last_request_at: float | None = None

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: Any) -> SecClient:
        config = settings.raw.get("sec", {})
        values: dict[str, Any] = {
            "user_agent": settings.sec_user_agent,
            "cache_dir": settings.sec_cache_dir,
            "requests_per_second": float(config.get("requests_per_second", 5.0)),
            "timeout_seconds": float(config.get("request_timeout_seconds", 20.0)),
            "retries": int(config.get("request_retries", 2)),
            "backoff_seconds": float(config.get("retry_backoff_seconds", 1.0)),
            "allow_stale_on_error": bool(config.get("allow_stale_cache_on_error", True)),
            "maximum_stale_hours": float(config.get("maximum_stale_cache_hours", 168.0)),
        }
        values.update(overrides)
        return cls(**values)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }

    def get_json(
        self,
        url: str,
        *,
        cache_key: str,
        ttl_hours: float,
        force: bool = False,
    ) -> SecJsonDocument:
        self._validate_url(url)
        cached = self._read_cache(url, cache_key)
        if cached and not force and self._is_fresh(cached.fetched_at, ttl_hours):
            return cached

        error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))
            self._throttle()
            try:
                response = self.session.get(
                    url,
                    headers=self.headers,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                error = exc
                continue

            if response.status_code == 200:
                try:
                    payload = response.json()
                except (requests.JSONDecodeError, ValueError) as exc:
                    error = SecPayloadError(f"SEC returned invalid JSON for {url}: {exc}")
                    continue
                fetched_at = self._now()
                self._write_cache(url, cache_key, fetched_at, payload)
                return SecJsonDocument(
                    payload=payload,
                    source_url=url,
                    fetched_at=fetched_at,
                    cache_hit=False,
                )

            detail = self._safe_response_detail(response)
            error = SecRequestError(
                f"SEC request failed with HTTP {response.status_code} for {url}{detail}"
            )
            if response.status_code not in SEC_RETRYABLE_STATUS_CODES:
                break

        if (
            cached
            and self.allow_stale_on_error
            and self._now() - cached.fetched_at <= timedelta(hours=self.maximum_stale_hours)
        ):
            return SecJsonDocument(
                payload=cached.payload,
                source_url=cached.source_url,
                fetched_at=cached.fetched_at,
                cache_hit=True,
                stale=True,
            )
        raise SecRequestError(f"SEC request failed after {self.retries + 1} attempt(s): {error}")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in SEC_ALLOWED_HOSTS:
            raise SecConfigurationError(
                "SEC requests must use HTTPS and an approved sec.gov data host."
            )

    def _throttle(self) -> None:
        minimum_interval = 1.0 / self.requests_per_second
        with self._request_lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                wait_for = minimum_interval - (now - self._last_request_at)
                if wait_for > 0:
                    self._sleep(wait_for)
                    now = self._monotonic()
            self._last_request_at = now

    def _cache_path(self, url: str, cache_key: str) -> Path:
        safe_key = re.sub(r"[^a-zA-Z0-9_-]+", "-", cache_key).strip("-") or "sec"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{safe_key}-{digest}.json"

    def _read_cache(self, url: str, cache_key: str) -> SecJsonDocument | None:
        path = self._cache_path(url, cache_key)
        if not path.exists():
            return None
        try:
            content = path.read_bytes()
            if content.startswith(b"\x1f\x8b"):
                content = gzip.decompress(content)
            record = json.loads(content.decode("utf-8"))
            if not isinstance(record, dict):
                return None
            schema_version = record.get("schema_version")
            if not isinstance(schema_version, int) or isinstance(schema_version, bool):
                return None
            if schema_version != SEC_CACHE_SCHEMA_VERSION:
                return None
            if record.get("source_url") != url:
                return None
            fetched_at = datetime.fromisoformat(record["fetched_at"])
            if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
                return None
            fetched_at = fetched_at.astimezone(UTC)
            now = self._now()
            if now.tzinfo is None or now.utcoffset() is None:
                return None
            if fetched_at > now.astimezone(UTC) + SEC_CLOCK_SKEW_TOLERANCE:
                return None
            return SecJsonDocument(
                payload=record["payload"],
                source_url=url,
                fetched_at=fetched_at,
                cache_hit=True,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, url: str, cache_key: str, fetched_at: datetime, payload: Any) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(url, cache_key)
        temp_path = path.with_suffix(".tmp")
        record = {
            "schema_version": SEC_CACHE_SCHEMA_VERSION,
            "source_url": url,
            "fetched_at": fetched_at.isoformat(),
            "payload": payload,
        }
        content = json.dumps(record, separators=(",", ":")).encode("utf-8")
        temp_path.write_bytes(gzip.compress(content, compresslevel=6))
        temp_path.replace(path)

    def _is_fresh(self, fetched_at: datetime, ttl_hours: float) -> bool:
        return fetched_at + timedelta(hours=ttl_hours) > self._now()

    @staticmethod
    def _safe_response_detail(response: Any) -> str:
        text = str(getattr(response, "text", "")).strip().replace("\n", " ")
        return f": {text[:200]}" if text else ""
