"""Offline contract tests for Yahoo responses at the external module boundary."""

import builtins
import sys
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from stockrank.data import yfinance_provider as adapter
from stockrank.models import PriceBar, Security

START = date(2026, 8, 1)
END = date(2026, 9, 1)
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
SECURITY = Security("AAA", "Configured company", "Configured sector")
OTHER = Security("BBB", "Other company", "Other sector")


@pytest.fixture(autouse=True)
def yahoo(monkeypatch):
    # Replacing the imported module prevents any real Yahoo client from being constructed.
    fake = SimpleNamespace(download=Mock(), Ticker=Mock())
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.setattr(adapter, "time", SimpleNamespace(sleep=Mock()))

    class Clock:
        @staticmethod
        def now(tz):
            assert tz == UTC
            return NOW

    monkeypatch.setattr(adapter, "datetime", Clock)
    return fake


def prices(rows=None):
    if rows is None:
        rows = [{"Open": 99, "High": 105, "Low": 98, "Close": 100, "Adj Close": 95, "Volume": 1234}]
    return pd.DataFrame(rows, index=pd.date_range("2026-08-28", periods=len(rows)))


@pytest.mark.parametrize("multi", [False, True])
def test_single_ticker_price_shapes_and_download_contract(yahoo, multi):
    frame = prices()
    yahoo.download.return_value = pd.concat({"AAA": frame}, axis=1) if multi else frame
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY], START, END)
    assert warnings == []
    assert result == {
        "AAA": [
            PriceBar(
                ticker="AAA",
                date=date(2026, 8, 28),
                open=99,
                high=105,
                low=98,
                close=100,
                adjusted_close=95,
                volume=1234,
                source="yfinance",
                fetched_at=NOW,
            )
        ]
    }
    yahoo.download.assert_called_once_with(
        tickers=["AAA"],
        start="2026-08-01",
        end="2026-09-01",
        auto_adjust=False,
        actions=False,
        group_by="ticker",
        threads=4,
        progress=False,
        timeout=20,
    )
    adapter.time.sleep.assert_not_called()


def test_batch_prices_keep_ticker_values_and_dates_separate(yahoo):
    yahoo.download.return_value = pd.concat(
        {
            "BBB": prices([{"Close": 200}, {"Close": 210}]),
            "AAA": prices([{"Close": 100}, {"Close": 110}]),
        },
        axis=1,
    )
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY, OTHER], START, END)
    assert warnings == []
    assert list(result) == ["AAA", "BBB"]
    assert [bar.close for bar in result["AAA"]] == [100, 110]
    assert [bar.close for bar in result["BBB"]] == [200, 210]
    for ticker, bars in result.items():
        assert [bar.date for bar in bars] == [date(2026, 8, 28), date(2026, 8, 29)]
        assert all(bar.ticker == ticker and bar.fetched_at == NOW for bar in bars)


@pytest.mark.parametrize("missing_kind", ["omitted", "unusable"])
def test_partial_batch_preserves_healthy_ticker_and_warns(yahoo, missing_kind):
    frames = {"AAA": prices()}
    if missing_kind == "unusable":
        frames["BBB"] = prices([{"Close": float("nan")}])
    yahoo.download.return_value = pd.concat(frames, axis=1)
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY, OTHER], START, END)
    assert set(result) == {"AAA"}
    assert warnings == [
        "BBB: "
        + (
            "missing from batch price response"
            if missing_kind == "omitted"
            else "no usable daily price bars"
        )
    ]
    assert yahoo.download.call_count == 2


@pytest.mark.parametrize(
    "invalid", [None, "not-a-number", float("nan"), float("inf"), -float("inf")]
)
def test_invalid_required_prices_skip_rows_optional_values_remain_missing(yahoo, invalid):
    yahoo.download.return_value = prices(
        [
            {"Close": invalid, "Adj Close": 90},
            {
                "Close": "100.5",
                "Adj Close": invalid,
                "Open": invalid,
                "High": invalid,
                "Low": invalid,
                "Volume": invalid,
            },
        ]
    )
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY], START, END)
    assert warnings == []
    assert result["AAA"] == [
        PriceBar(
            ticker="AAA",
            date=date(2026, 8, 29),
            open=None,
            high=None,
            low=None,
            close=100.5,
            adjusted_close=100.5,
            volume=None,
            source="yfinance",
            fetched_at=NOW,
        )
    ]


def test_missing_price_columns_and_timezone_aware_dates(yahoo):
    frame = prices([{"Close": 101}, {"Open": 90}])
    frame.index = frame.index.tz_localize("America/New_York")
    yahoo.download.return_value = frame
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY], START, END)
    assert warnings == []
    assert len(result["AAA"]) == 1
    bar = result["AAA"][0]
    assert (bar.date, bar.close, bar.adjusted_close, bar.volume) == (
        date(2026, 8, 28),
        101,
        101,
        None,
    )


@pytest.mark.parametrize("first", [None, pd.DataFrame(), OSError("temporary outage")])
def test_price_retry_recovers_without_warning(yahoo, first):
    yahoo.download.side_effect = [first, prices()]
    result, warnings = adapter.YFinanceProvider(retries=3, backoff_seconds=0.25).fetch_prices(
        [SECURITY], START, END
    )
    assert set(result) == {"AAA"}
    assert warnings == []
    assert yahoo.download.call_count == 2
    adapter.time.sleep.assert_called_once_with(0.25)


@pytest.mark.parametrize("failure", [None, pd.DataFrame(), OSError("offline")])
def test_exhausted_price_retries_raise_and_do_not_sleep_after_last_attempt(yahoo, failure):
    yahoo.download.side_effect = [failure] * 3
    with pytest.raises(RuntimeError, match="Yahoo price download returned no data") as caught:
        adapter.YFinanceProvider(retries=3, backoff_seconds=0.25).fetch_prices(
            [SECURITY], START, END
        )
    if isinstance(failure, Exception):
        assert "offline" in str(caught.value)
    assert yahoo.download.call_count == 3
    assert adapter.time.sleep.call_args_list == [((0.25,),), ((0.5,),)]


def test_fundamental_field_mapping_and_metadata(yahoo):
    yahoo.Ticker.return_value.get_info.return_value = {
        "longName": "Yahoo company",
        "shortName": "Short name",
        "sector": "Technology",
        "industry": "Software",
        "marketCap": "123456",
        "revenueGrowth": 0.1,
        "earningsGrowth": -0.2,
        "freeCashflow": 345,
        "totalRevenue": 678,
        "forwardPE": 12,
        "trailingPE": 13,
        "pegRatio": 1.4,
        "priceToSalesTrailing12Months": 2.5,
        "grossMargins": 0.6,
        "profitMargins": 0.3,
        "returnOnEquity": 0.4,
        "debtToEquity": 55,
        "currentRatio": 1.6,
        "beta": 1.7,
        "currentPrice": 98,
        "regularMarketPrice": 97,
    }
    snapshot, warnings = adapter.YFinanceProvider().fetch_fundamental(SECURITY)
    assert warnings == []
    assert snapshot.to_dict() == {
        "ticker": "AAA",
        "source": "yfinance",
        "fetched_at": NOW.isoformat(),
        "company": "Yahoo company",
        "sector": "Technology",
        "industry": "Software",
        "market_cap": 123456,
        "revenue_growth": 0.1,
        "earnings_growth": -0.2,
        "free_cash_flow": 345,
        "total_revenue": 678,
        "forward_pe": 12,
        "trailing_pe": 13,
        "peg_ratio": 1.4,
        "price_to_sales": 2.5,
        "gross_margin": 0.6,
        "profit_margin": 0.3,
        "return_on_equity": 0.4,
        "debt_to_equity": 55,
        "current_ratio": 1.6,
        "beta": 1.7,
        "provider_price": 98,
    }
    yahoo.Ticker.assert_called_once_with("AAA")
    yahoo.Ticker.return_value.get_info.assert_called_once_with()
    adapter.time.sleep.assert_not_called()


@pytest.mark.parametrize(
    "names,company",
    [
        ({"shortName": "Short"}, "Short"),
        ({"longName": "", "shortName": ""}, "Configured company"),
    ],
)
def test_sparse_fundamentals_preserve_identity_and_fallback_price(yahoo, names, company):
    yahoo.Ticker.return_value.get_info.return_value = {
        **names,
        "regularMarketPrice": "123.5",
        "beta": 0,
        "marketCap": float("inf"),
        "trailingPE": "bad",
        "forwardPE": None,
        "revenueGrowth": float("nan"),
    }
    snapshot, warnings = adapter.YFinanceProvider().fetch_fundamental(SECURITY)
    assert (snapshot.company, snapshot.sector, snapshot.industry) == (
        company,
        SECURITY.sector,
        None,
    )
    assert snapshot.provider_price == 123.5
    assert snapshot.beta == 0
    assert snapshot.market_cap is snapshot.trailing_pe is snapshot.forward_pe is None
    assert snapshot.revenue_growth is None
    assert warnings == ["AAA: sparse fundamental summary (2 fields)"]


@pytest.mark.parametrize("count", [4, 5])
def test_sparse_warning_threshold_counts_only_available_numeric_fields(yahoo, count):
    info = dict(
        list(
            {
                "marketCap": 1,
                "revenueGrowth": 0,
                "earningsGrowth": -1,
                "freeCashflow": 2,
                "beta": 3,
            }.items()
        )[:count]
    )
    info.update(longName="Name", sector="Sector", industry="Industry")
    yahoo.Ticker.return_value.get_info.return_value = info
    snapshot, warnings = adapter.YFinanceProvider().fetch_fundamental(SECURITY)
    assert snapshot is not None
    assert warnings == (["AAA: sparse fundamental summary (4 fields)"] if count == 4 else [])


@pytest.mark.parametrize("failure", [None, {}, OSError("offline")])
def test_fundamental_retries_recover_or_return_explicit_unavailable_warning(yahoo, failure):
    get_info = yahoo.Ticker.return_value.get_info
    get_info.side_effect = [failure, {"marketCap": 10}]
    provider = adapter.YFinanceProvider(retries=2, backoff_seconds=0.25)
    snapshot, warnings = provider.fetch_fundamental(SECURITY)
    assert snapshot.market_cap == 10
    assert warnings == ["AAA: sparse fundamental summary (1 fields)"]
    assert get_info.call_count == 2
    adapter.time.sleep.assert_called_once_with(0.25)

    get_info.reset_mock(side_effect=True)
    adapter.time.sleep.reset_mock()
    get_info.side_effect = [failure] * 3
    snapshot, warnings = adapter.YFinanceProvider(
        retries=3, backoff_seconds=0.25
    ).fetch_fundamental(SECURITY)
    assert snapshot is None
    assert len(warnings) == 1 and "AAA: fundamental summary unavailable" in warnings[0]
    if isinstance(failure, Exception):
        assert "offline" in warnings[0]
    assert get_info.call_count == 3
    assert adapter.time.sleep.call_args_list == [((0.25,),), ((0.5,),)]


def test_ticker_construction_failures_are_retried(yahoo):
    yahoo.Ticker.side_effect = OSError("client unavailable")
    snapshot, warnings = adapter.YFinanceProvider().fetch_fundamental(SECURITY)
    assert snapshot is None
    assert "client unavailable" in warnings[0]
    assert yahoo.Ticker.call_count == 2


@pytest.mark.parametrize("method", ["prices", "fundamentals"])
def test_nonpositive_retry_settings_still_attempt_once_without_sleep(yahoo, method):
    provider = adapter.YFinanceProvider(retries=0, backoff_seconds=-1)
    assert provider.backoff_seconds == 0
    if method == "prices":
        yahoo.download.return_value = None
        with pytest.raises(RuntimeError):
            provider.fetch_prices([SECURITY], START, END)
        yahoo.download.assert_called_once()
    else:
        yahoo.Ticker.return_value.get_info.return_value = {}
        assert provider.fetch_fundamental(SECURITY)[0] is None
        yahoo.Ticker.assert_called_once()
    adapter.time.sleep.assert_not_called()


def test_missing_yfinance_dependency_has_actionable_error(monkeypatch):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match="yfinance is not installed.*pip install") as caught:
        adapter.YFinanceProvider._module()
    assert isinstance(caught.value.__cause__, ImportError)


def test_partial_price_retry_fetches_only_missing_tickers_serially(yahoo):
    yahoo.download.side_effect = [pd.concat({"AAA": prices()}, axis=1), prices([{"Close": 200}])]
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY, OTHER], START, END)
    assert warnings == []
    assert result["AAA"][0].close == 100
    assert result["BBB"][0].close == 200
    calls = yahoo.download.call_args_list
    assert calls[0].kwargs["tickers"] == ["AAA", "BBB"]
    assert calls[0].kwargs["threads"] == 4
    assert calls[1].kwargs["tickers"] == ["BBB"]
    assert calls[1].kwargs["threads"] is False


def test_failed_retry_preserves_healthy_prices_and_reports_remaining_failure(yahoo):
    yahoo.download.side_effect = [
        pd.concat({"AAA": prices()}, axis=1),
        OSError("database unavailable"),
    ]
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY, OTHER], START, END)
    assert set(result) == {"AAA"}
    assert any("BBB" in warning for warning in warnings)
    assert any("database unavailable" in warning for warning in warnings)


def test_empty_universe_does_not_download(yahoo):
    assert adapter.YFinanceProvider().fetch_prices([], START, END) == ({}, [])
    yahoo.download.assert_not_called()


def test_ambiguous_flat_batch_cannot_assign_one_stocks_prices_to_others(yahoo):
    yahoo.download.return_value = prices()
    result, warnings = adapter.YFinanceProvider().fetch_prices([SECURITY, OTHER], START, END)
    assert result == {}
    assert any("ambiguous" in warning for warning in warnings)
    assert yahoo.download.call_count == 2
