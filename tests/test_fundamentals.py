"""Concurrency, cache/fallback equivalence and owner-thread/cancellation boundaries."""

import copy
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from stockrank.config import Settings, load_settings
from stockrank.data.yfinance_provider import YFinanceProvider
from stockrank.fundamentals import refresh_fundamentals
from stockrank.models import FundamentalSnapshot, Security
from stockrank.storage import Storage


@pytest.fixture
def context(tmp_path):
    loaded = load_settings()
    raw = copy.deepcopy(loaded.raw)
    raw["app"]["runtime_dir"] = "runtime"
    settings = Settings(
        root=tmp_path,
        raw=raw,
        universe=tuple(Security(f"T{i:03}", f"Test {i}", "Industrials") for i in range(8)),
    )
    return settings, datetime.now(UTC)


class OwnedStorage(Storage):
    def __init__(self, path):
        super().__init__(path)
        self.owner = threading.get_ident()
        self.writes = []

    def get_fundamental(self, *args, **kwargs):
        assert threading.get_ident() == self.owner
        return super().get_fundamental(*args, **kwargs)

    def put_fundamental(self, value, ttl_hours):
        assert threading.get_ident() == self.owner
        self.writes.append(value.ticker)
        super().put_fundamental(value, ttl_hours)


def storage_at(settings, suffix):
    storage = OwnedStorage(settings.runtime_dir / f"{suffix}.sqlite3")
    storage.initialize()
    return storage


def snapshot(ticker, now):
    return FundamentalSnapshot(ticker=ticker, source="yfinance", fetched_at=now, market_cap=100)


@pytest.mark.parametrize("size", [50, 100])
def test_serial_and_concurrent_results_match_for_partial_failures(context, size):
    settings, now = context
    settings = replace(
        settings,
        universe=tuple(Security(f"T{i:03}", f"Test {i}", "Industrials") for i in range(size)),
    )
    results = []
    for workers in (1, 3):
        storage = storage_at(settings, str(workers))
        # Fresh, usable stale, overage stale and absent values are all exercised.
        for index, security in enumerate(settings.universe):
            if index % 4 == 0:
                storage.put_fundamental(snapshot(security.ticker, now), 24)
            elif index % 4 in (1, 2):
                days = 2 if index % 4 == 1 else 10
                storage.put_fundamental(snapshot(security.ticker, now - timedelta(days=days)), 1)
        storage.writes.clear()
        calls = []
        lock = threading.Lock()

        def fetch(security, lock=lock, calls=calls):
            with lock:
                calls.append(security.ticker)
            index = int(security.ticker[1:])
            if index % 4 == 1:
                raise OSError("controlled failure")
            if index % 4 == 2:
                return None, [f"{security.ticker}: unavailable"]
            return snapshot(security.ticker, now), [f"{security.ticker}: fixture warning"]

        owner = threading.get_ident()
        progress = []

        def report(message, owner=owner, progress=progress):
            assert threading.get_ident() == owner
            progress.append(message)

        result = refresh_fundamentals(
            settings,
            storage,
            SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
            now=now,
            workers=workers,
            progress=report,
        )
        assert set(calls) == {s.ticker for i, s in enumerate(settings.universe) if i % 4}
        assert len(calls) == len(set(calls))
        assert storage.writes == [s.ticker for i, s in enumerate(settings.universe) if i % 4 == 3]
        assert result.lineage["T000"]["status"] == "fresh_cache"
        assert result.lineage["T001"]["status"] == "stale_fallback"
        assert result.lineage["T002"]["status"] == "rejected_stale"
        assert result.lineage["T003"]["status"] == "refreshed"
        assert len(progress) == 1 + len(calls)
        results.append(result)
    assert results[0] == results[1]


def test_three_requests_overlap_but_merge_in_universe_order(context):
    settings, now = context
    settings = replace(settings, universe=settings.universe[:3])
    storage = storage_at(settings, "overlap")
    ready = threading.Barrier(3, timeout=5)
    second_finished = threading.Event()
    thread_ids = set()
    lock = threading.Lock()

    def fetch(security):
        with lock:
            thread_ids.add(threading.get_ident())
        ready.wait()
        if security.ticker == "T000":
            assert second_finished.wait(5)
        elif security.ticker == "T001":
            second_finished.set()
        return snapshot(security.ticker, now), [security.ticker]

    result = refresh_fundamentals(
        settings,
        storage,
        SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
        now=now,
        workers=3,
    )
    assert len(thread_ids) == 3
    assert storage.owner not in thread_ids
    assert result.warnings == ["T000", "T001", "T002"]
    assert storage.writes == result.warnings


@pytest.mark.parametrize("force", [False, True])
def test_warm_cache_and_force(context, force):
    settings, now = context
    storage = storage_at(settings, "warm")
    for security in settings.universe:
        storage.put_fundamental(snapshot(security.ticker, now), 24)
    storage.writes.clear()
    calls = []
    lock = threading.Lock()

    def fetch(security):
        with lock:
            calls.append(security.ticker)
        return snapshot(security.ticker, now), []

    result = refresh_fundamentals(
        settings,
        storage,
        SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
        now=now,
        force=force,
        workers=3,
    )
    assert len(calls) == (len(settings.universe) if force else 0)
    assert {r["status"] for r in result.lineage.values()} == {
        "refreshed" if force else "fresh_cache"
    }


@pytest.mark.parametrize("workers", [1, 3])
def test_expiry_between_selection_and_consumption_refreshes(context, monkeypatch, workers):
    settings, now = context
    storage = storage_at(settings, "expiry")
    for security in settings.universe:
        storage.put_fundamental(snapshot(security.ticker, now), 24)
    original = storage.get_fundamental
    reads = {}

    def read(ticker, source, fresh_only=False):
        reads[ticker] = reads.get(ticker, 0) + 1
        if ticker == "T003" and reads[ticker] > 1 and fresh_only:
            return None
        return original(ticker, source, fresh_only=fresh_only)

    monkeypatch.setattr(storage, "get_fundamental", read)
    calls = []

    def fetch(security):
        calls.append(security.ticker)
        return snapshot(security.ticker, now), []

    result = refresh_fundamentals(
        settings,
        storage,
        SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
        now=now,
        workers=workers,
    )
    assert calls == ["T003"]
    assert result.lineage["T003"]["status"] == "refreshed"


@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError])
def test_interruption_stops_scheduling_drains_workers_and_stops_writes(context, failure):
    settings, now = context
    storage = storage_at(settings, "cancel")
    ready = threading.Barrier(3, timeout=5)
    calls = []
    lock = threading.Lock()

    def fetch(security):
        with lock:
            calls.append(security.ticker)
        ready.wait()
        return snapshot(security.ticker, now), []

    def progress(message):
        if "1/8 checked" in message:
            raise failure("stop")

    with pytest.raises(failure):
        refresh_fundamentals(
            settings,
            storage,
            SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
            now=now,
            workers=3,
            progress=progress,
        )
    assert sorted(calls) == ["T000", "T001", "T002"]
    assert storage.writes == ["T000"]
    assert not any(t.name.startswith("stockrank-fundamentals") for t in threading.enumerate())


@pytest.mark.parametrize("workers", [1, 3])
def test_yahoo_throttling_preserves_retry_budget_and_fallback(context, monkeypatch, workers):
    settings, now = context
    storage = storage_at(settings, "throttled")
    storage.put_fundamental(snapshot("T000", now - timedelta(days=2)), 1)
    attempts = {}
    lock = threading.Lock()

    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker

        def get_info(self):
            with lock:
                attempts[self.ticker] = attempts.get(self.ticker, 0) + 1
                attempt = attempts[self.ticker]
            if self.ticker == "T000" or attempt == 1:
                raise RuntimeError("HTTP 429 Too Many Requests")
            return {"marketCap": 100}

    monkeypatch.setattr(
        YFinanceProvider, "_module", staticmethod(lambda: SimpleNamespace(Ticker=Ticker))
    )
    result = refresh_fundamentals(
        settings,
        storage,
        YFinanceProvider(retries=2, backoff_seconds=0),
        now=now,
        workers=workers,
    )
    assert set(attempts.values()) == {2}
    assert result.lineage["T000"]["status"] == "stale_fallback"
    assert "429" in result.warnings[0]
    assert len(result.snapshots) == 8
    assert all(result.lineage[f"T{i:03}"]["status"] == "refreshed" for i in range(1, 8))


@pytest.mark.parametrize("workers", [0, 4])
def test_worker_limit_is_bounded(context, workers):
    settings, now = context
    storage = storage_at(settings, "limit")
    with pytest.raises(ValueError, match="one to three"):
        refresh_fundamentals(settings, storage, YFinanceProvider(), now=now, workers=workers)


def test_cancellation_waits_for_running_calls_without_merging_them(context):
    settings, now = context
    storage = storage_at(settings, "drain")
    ready = threading.Barrier(3, timeout=5)
    release = threading.Event()
    done = []

    def fetch(security):
        ready.wait()
        if security.ticker != "T000":
            assert release.wait(5)
            done.append(security.ticker)
        return snapshot(security.ticker, now), []

    timer = threading.Timer(0.05, release.set)

    def progress(message):
        if "1/8 checked" in message:
            timer.start()
            raise KeyboardInterrupt

    try:
        with pytest.raises(KeyboardInterrupt):
            refresh_fundamentals(
                settings,
                storage,
                SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
                now=now,
                workers=3,
                progress=progress,
            )
        assert sorted(done) == ["T001", "T002"]
        assert storage.writes == ["T000"]
    finally:
        release.set()
        timer.cancel()
        if timer.ident is not None:
            timer.join()


def test_worker_interrupt_propagates_without_cache_fallback(context):
    settings, now = context
    storage = storage_at(settings, "worker-interrupt")
    ready = threading.Barrier(3, timeout=5)
    calls = []

    def fetch(security):
        calls.append(security.ticker)
        ready.wait()
        if security.ticker == "T000":
            raise KeyboardInterrupt
        return snapshot(security.ticker, now), []

    with pytest.raises(KeyboardInterrupt):
        refresh_fundamentals(
            settings,
            storage,
            SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
            now=now,
            workers=3,
        )
    assert sorted(calls) == ["T000", "T001", "T002"]
    assert not storage.writes


def test_database_failure_stops_further_requests(context, monkeypatch):
    settings, now = context
    storage = storage_at(settings, "write-failure")
    ready = threading.Barrier(3, timeout=5)
    calls = []

    def fetch(security):
        calls.append(security.ticker)
        ready.wait()
        return snapshot(security.ticker, now), []

    def fail_write(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "put_fundamental", fail_write)
    with pytest.raises(OSError, match="disk full"):
        refresh_fundamentals(
            settings,
            storage,
            SimpleNamespace(name="yfinance", fetch_fundamental=fetch),
            now=now,
            workers=3,
        )
    assert sorted(calls) == ["T000", "T001", "T002"]
    assert not storage.writes


@pytest.mark.parametrize("flag", [True, False, None])
def test_concurrent_yahoo_requires_known_thread_safe_backend(monkeypatch, flag):
    module = SimpleNamespace(_http=SimpleNamespace(HAS_CURL_CFFI=flag))
    monkeypatch.setattr(YFinanceProvider, "_module", staticmethod(lambda: module))
    assert YFinanceProvider().supports_concurrent_fundamentals() is (flag is True)
    monkeypatch.setattr(YFinanceProvider, "_module", staticmethod(lambda: SimpleNamespace()))
    assert not YFinanceProvider().supports_concurrent_fundamentals()


def test_missing_yahoo_module_keeps_serial_cache_fallback(monkeypatch):
    def unavailable():
        raise RuntimeError("yfinance is not installed")

    monkeypatch.setattr(YFinanceProvider, "_module", staticmethod(unavailable))
    assert not YFinanceProvider().supports_concurrent_fundamentals()
