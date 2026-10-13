"""Bounded network refresh; cache decisions and writes stay on the calling thread."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypedDict

from stockrank.config import Settings
from stockrank.data.base import MarketDataProvider
from stockrank.models import FundamentalSnapshot, Security
from stockrank.storage import Storage


class FundamentalLineage(TypedDict):
    status: str
    source: str
    fetched_at: str | None
    age_hours: float | None


@dataclass
class FundamentalRefresh:
    snapshots: dict[str, FundamentalSnapshot] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    lineage: dict[str, FundamentalLineage] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _fetch(
    provider: MarketDataProvider, security: Security
) -> tuple[FundamentalSnapshot | None, list[str]]:
    try:
        return provider.fetch_fundamental(security)
    except Exception as error:  # noqa: BLE001 - preserve per-ticker cache fallback.
        return None, [f"{security.ticker}: fundamental refresh failed ({error})"]


@contextmanager
def _fetch_pool(workers: int) -> Iterator[ThreadPoolExecutor | None]:
    if workers == 1:
        yield None
        return
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stockrank-fundamentals")
    try:
        yield executor
    finally:
        # In-flight HTTP calls cannot be killed safely. Drain running calls;
        # discard their unmerged results and cancel anything not started.
        executor.shutdown(wait=True, cancel_futures=True)


def _fetch_ordered(
    provider: MarketDataProvider,
    securities: Sequence[Security],
    workers: int,
    executor: ThreadPoolExecutor | None,
) -> Iterator[tuple[FundamentalSnapshot | None, list[str]]]:
    if executor is None:
        for security in securities:
            yield _fetch(provider, security)
        return
    if not securities:
        return
    pending = deque(
        executor.submit(_fetch, provider, security) for security in securities[:workers]
    )
    remaining = iter(securities[workers:])
    while pending:
        yield pending.popleft().result()
        # Refill only after the caller has merged the previous result. Never
        # queue the entire universe, including during cancellation or failure.
        next_security = next(remaining, None)
        if next_security is not None:
            pending.append(executor.submit(_fetch, provider, next_security))


def refresh_fundamentals(
    settings: Settings,
    storage: Storage,
    provider: MarketDataProvider,
    *,
    now: datetime,
    force: bool = False,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
) -> FundamentalRefresh:
    """Refresh only cache misses; preserve universe order and existing age limits.

    Concurrent callers must supply a thread-safe provider. The serial path avoids
    starting threads entirely. No application database access occurs in workers.
    """
    if not 1 <= workers <= 3:
        raise ValueError("Fundamental refresh supports one to three workers")
    result = FundamentalRefresh()
    ttl = float(settings.raw["provider"]["fundamental_cache_ttl_hours"])
    maximum_age = float(settings.raw["provider"]["maximum_stale_fundamental_hours"])
    cached_snapshots: dict[str, FundamentalSnapshot] = {}
    pending: list[Security] = []
    for security in settings.universe:
        cached = (
            None
            if force
            else storage.get_fundamental(security.ticker, provider.name, fresh_only=True)
        )
        if cached:
            age = max(0.0, (now - cached.fetched_at.astimezone(UTC)).total_seconds() / 3600)
            if age <= maximum_age:
                cached_snapshots[security.ticker] = cached
                continue
        pending.append(security)
    if progress:
        progress(
            f"Fundamentals: {len(cached_snapshots)}/{len(settings.universe)} cached; "
            f"{len(pending)} to refresh (up to {min(workers, len(pending))} at a time)"
        )
    completed = 0
    refresh_total = len(pending)
    with _fetch_pool(workers) as executor:
        refreshed = _fetch_ordered(provider, pending, workers, executor)
        for security in settings.universe:
            cached = cached_snapshots.get(security.ticker)
            was_cached = cached is not None
            if cached:
                # Earlier network waits can cross a cache expiry. Recheck on the
                # owner thread at consumption, as the original serial loop did.
                cached = storage.get_fundamental(security.ticker, provider.name, fresh_only=True)
                if (
                    cached
                    and (now - cached.fetched_at.astimezone(UTC)).total_seconds() / 3600
                    > maximum_age
                ):
                    cached = None
            if cached:
                age = max(0.0, (now - cached.fetched_at.astimezone(UTC)).total_seconds() / 3600)
                result.snapshots[security.ticker] = cached
                result.lineage[security.ticker] = {
                    "status": "fresh_cache",
                    "source": cached.source,
                    "fetched_at": cached.fetched_at.isoformat(),
                    "age_hours": round(age, 3),
                }
                continue
            if was_cached:
                refresh_total += 1
                # A late cache miss shares the same bounded pool; it never adds
                # an extra network thread. At most one extra task can be queued.
                snapshot, warnings = (
                    executor.submit(_fetch, provider, security).result()
                    if executor is not None
                    else _fetch(provider, security)
                )
            else:
                snapshot, warnings = next(refreshed)
            result.warnings.extend(warnings)
            if snapshot:
                storage.put_fundamental(snapshot, ttl)
                result.snapshots[security.ticker] = snapshot
                result.lineage[security.ticker] = {
                    "status": "refreshed",
                    "source": snapshot.source,
                    "fetched_at": snapshot.fetched_at.isoformat(),
                    "age_hours": 0.0,
                }
            else:
                stale = storage.get_fundamental(security.ticker, provider.name, fresh_only=False)
                if stale:
                    age = max(0.0, (now - stale.fetched_at.astimezone(UTC)).total_seconds() / 3600)
                    if age <= maximum_age:
                        result.snapshots[security.ticker] = stale
                        note = (
                            f"Using stale fundamentals fetched {stale.fetched_at.isoformat()} "
                            f"({age:.1f} hours old)"
                        )
                        status = "stale_fallback"
                    else:
                        note = (
                            f"Rejected fundamentals fetched {stale.fetched_at.isoformat()} because "
                            f"they are {age:.1f} hours old, above the {maximum_age:g}-hour limit"
                        )
                        status = "rejected_stale"
                    result.notes.setdefault(security.ticker, []).append(note)
                    result.warnings.append(f"{security.ticker}: {note.lower()}")
                    result.lineage[security.ticker] = {
                        "status": status,
                        "source": stale.source,
                        "fetched_at": stale.fetched_at.isoformat(),
                        "age_hours": round(age, 3),
                    }
                else:
                    result.lineage[security.ticker] = {
                        "status": "unavailable",
                        "source": provider.name,
                        "fetched_at": None,
                        "age_hours": None,
                    }
            completed += 1
            if progress:
                progress(
                    f"Fundamentals: {completed}/{refresh_total} checked - "
                    f"{security.ticker}: {result.lineage[security.ticker]['status']}"
                )
    return result
