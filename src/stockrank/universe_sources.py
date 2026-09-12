"""Public listing/SEC identity gates and live data for bounded universe discovery."""

from __future__ import annotations

import csv
import io
import re
import statistics
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import requests

from stockrank.config import TICKER_PATTERN, Settings
from stockrank.customization import YAHOO_SECTOR_MAP
from stockrank.data import YFinanceProvider
from stockrank.data.sec import SecClient, SecIdentityDirectory, SecSubmissions, normalize_sec_ticker
from stockrank.freshness import assess_price_bars
from stockrank.metrics import apply_sector_conventions, calculate_metrics
from stockrank.models import Security
from stockrank.price_integrity import assess_price_series, build_reference_sessions
from stockrank.scoring import score_universe
from stockrank.universe_discovery import DiscoveryPolicy

LISTING_URLS = (
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
)


def listing_rows(text: str, *, nasdaq: bool, now: datetime) -> dict[str, dict]:
    stamp = re.search(r"File Creation Time:\s*(\d{8})", text)
    if not stamp:
        raise ValueError("Listing file has no creation date")
    created = datetime.strptime(stamp[1], "%m%d%Y").replace(tzinfo=UTC).date()
    if not 0 <= (now.date() - created).days <= 5:
        raise ValueError("Listing directory is stale or future-dated")
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    required = {"Security Name", "ETF", "Test Issue", "Symbol" if nasdaq else "ACT Symbol"}
    required.add("Financial Status" if nasdaq else "Exchange")
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Listing directory schema changed")
    result = {}
    for row in reader:
        symbol = row.get("Symbol" if nasdaq else "ACT Symbol", "")
        if not symbol or symbol.startswith("File Creation"):
            continue
        # Yahoo uses a hyphen for common share classes. Other suffixes stay unsupported.
        ticker = symbol.replace(".", "-")
        if TICKER_PATTERN.fullmatch(ticker):
            result[ticker] = {**row, "listing_date": created.isoformat(), "nasdaq": nasdaq}
    if not result:
        raise ValueError("Listing directory is empty")
    return result


def listing_exclusions(row: dict | None) -> list[str]:
    if row is None:
        return ["Not resolved in the current supported exchange directory"]
    reasons = []
    if row.get("ETF") != "N" or row.get("Test Issue") != "N":
        reasons.append("ETF, test security, or unknown listing classification")
    if row.get("nasdaq"):
        if row.get("Financial Status") != "N":
            reasons.append("Nasdaq listing has a financial-status warning")
    elif row.get("Exchange") not in {"N", "A"}:
        reasons.append("Exchange outside initial Nasdaq/NYSE/NYSE American scope")
    name = row.get("Security Name", "").lower()
    if not re.search(r"\b(common (stock|shares)|ordinary shares)\b", name):
        reasons.append("Security name does not establish supported common equity")
    if re.search(r"\b(preferred|depositary|warrants?|units?|rights?|fund|etf)\b", name):
        reasons.append(
            "Unsupported security type (fund, depositary, preferred, unit or derivative)"
        )
    return reasons


def submission_checks(
    payload: dict, ticker: str, *, today: date, annual_history_confirmed: bool = False
) -> tuple[list[str], list[str]]:
    reasons, warnings = [], []
    if normalize_sec_ticker(ticker) not in {
        normalize_sec_ticker(t) for t in payload.get("tickers", [])
    }:
        reasons.append(
            "Ticker not confirmed by SEC submissions; identity/corporate action unresolved"
        )
    if str(payload.get("sic", "")) in {"6770", "6722", "6726"}:
        reasons.append("SEC classifies issuer as blank-check company or fund")
    recent = payload.get("filings", {}).get("recent", {})
    forms, dates = recent.get("form", []), recent.get("filingDate", [])
    if not forms or len(forms) != len(dates):
        return reasons + ["SEC filing history missing or malformed"], warnings
    dated = [(form, date.fromisoformat(day)) for form, day in zip(forms, dates)]
    relevant = [(form, day) for form, day in dated if 0 <= (today - day).days <= 550]
    if not annual_history_confirmed and not any(form in {"10-K", "10-K/A"} for form, _ in relevant):
        reasons.append(
            "No recent 10-K: foreign, transitional, or unverified reporting is unsupported"
        )
    if any(form in {"20-F", "40-F", "6-K", "10-KT", "10-QT"} for form, _ in relevant):
        reasons.append("Foreign or transitional filing normalization is outside initial scope")
    if any(form.startswith(("25", "15-")) for form, day in relevant if (today - day).days <= 365):
        warnings.append(
            "Review recent SEC deregistration/delisting forms: they may concern debt or another security, not this common stock"
        )
    if any(
        form in {"S-4", "S-4/A", "DEFM14A"} for form, day in relevant if (today - day).days <= 180
    ):
        warnings.append(
            "Review recent merger-related SEC forms: issuer may be an acquirer; this does not establish a pending removal"
        )
    if payload.get("formerNames"):
        warnings.append("SEC records former company names; inspect identity history")
    return reasons, warnings


def history_checks(bars, reference) -> tuple[list[str], float | None]:
    """Eligibility uses completed data only; callers apply the shared freshness gate first."""
    reasons = []
    if not bars or not reference:
        return ["Completed history or market reference unavailable"], None
    if len({b.date for b in bars}) < 300 or assess_price_series(bars, reference).status == "gapped":
        reasons.append("Requires 300 trading sessions and continuous recent history")
    volumes = [b.close * b.volume for b in bars[-20:] if b.volume is not None and b.volume > 0]
    liquidity = statistics.median(volumes) if len(volumes) == 20 else None
    if liquidity is None or liquidity < 10_000_000 or bars[-1].close < 5:
        reasons.append("Requires price >= $5 and 20-session median dollar volume >= $10m")
    if bars[-1].date != reference[-1]:
        reasons.append("Latest completed date differs from market reference")
    return reasons, liquidity


def collect_evidence(
    settings: Settings, policy: DiscoveryPolicy, *, now: datetime, progress=print
) -> dict:
    """No ranking DB writes or production analysis runs. Failures remain explicit."""
    yf = YFinanceProvider._module()
    yf.set_tz_cache_location(str(settings.runtime_dir / "cache" / "yfinance-discovery"))
    listings, sources, errors = {}, [], []
    for index, url in enumerate(LISTING_URLS):
        progress(f"Checking listing directory {index + 1}/2...", flush=True)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        listings.update(listing_rows(response.text, nasdaq=index == 0, now=now))
        sources.append({"url": url, "fetched_at": datetime.now(UTC).isoformat()})
    client = SecClient.from_settings(settings, allow_stale_on_error=False)
    directory = SecIdentityDirectory.from_settings(settings, client).fetch(force=True)
    sources.append({"url": directory.source_url, "fetched_at": directory.fetched_at.isoformat()})
    identities = defaultdict(list)
    for identity in directory.identities:
        identities[identity.ticker].append(identity)
    pool = {s.ticker: s for s in settings.universe}
    for ticker in policy.consider_tickers:
        pool.setdefault(ticker, Security(ticker, ticker, ""))
    screens = []
    for yahoo_sector, sector in YAHOO_SECTOR_MAP.items():
        progress(f"Discovering {sector} candidates...", flush=True)
        query = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("eq", ["region", "us"]),
                yf.EquityQuery("eq", ["sector", yahoo_sector]),
                yf.EquityQuery("gte", ["intradayprice", 5]),
                yf.EquityQuery("is-in", ["exchange", "NMS", "NGM", "NCM", "NYQ", "ASE"]),
            ],
        )
        response = yf.screen(
            query, size=policy.per_sector, sortField="intradaymarketcap", sortAsc=False
        )
        quotes = response.get("quotes") if isinstance(response, dict) else None
        if not quotes:
            raise ValueError(f"Yahoo screener returned no candidates for {sector}")
        screens.append(
            {
                "sector": sector,
                "total": response.get("total"),
                "symbols": [q.get("symbol") for q in quotes],
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        )
        for quote in quotes:
            ticker = quote.get("symbol", "")
            if TICKER_PATTERN.fullmatch(ticker) and quote.get("quoteType") == "EQUITY":
                pool.setdefault(
                    ticker,
                    Security(
                        ticker, quote.get("longName") or quote.get("shortName") or ticker, sector
                    ),
                )
    if len(pool) > 11 * policy.per_sector + len(settings.universe) + len(policy.consider_tickers):
        raise ValueError("Discovery pool exceeded bound")
    rows, candidates = [], []
    for security in sorted(pool.values(), key=lambda s: s.ticker):
        reasons = listing_exclusions(listings.get(security.ticker))
        matched = identities.get(normalize_sec_ticker(security.ticker), [])
        if len(matched) != 1:
            reasons.append("SEC identity missing or ambiguous; no automatic identity override")
        elif matched[0].exchange not in {"Nasdaq", "NYSE", "NYSE American"}:
            reasons.append("SEC exchange is outside supported scope")
        row = {
            "ticker": security.ticker,
            "company": security.company,
            "sector": security.sector,
            "cik": matched[0].cik if len(matched) == 1 else None,
            "listing": listings.get(security.ticker),
            "exclusions": reasons,
            "warnings": [],
            "score": None,
            "coverage": None,
        }
        rows.append(row)
        if not reasons:
            candidates.append(security)
    progress(f"Checking history for {len(candidates)} supported listings...", flush=True)
    provider = YFinanceProvider(retries=1)
    prices, price_warnings = provider.fetch_prices(
        candidates + [Security("SPY", "Market calendar reference", "")],
        now.date() - timedelta(days=550),
        now.date() + timedelta(days=1),
    )
    fresh = {}
    for ticker, bars in prices.items():
        fresh[ticker] = assess_price_bars(
            bars,
            now=datetime.now(UTC),
            timezone_name=settings.raw["app"]["timezone"],
            completion_buffer_minutes=settings.raw["provider"][
                "daily_bar_completion_buffer_minutes"
            ],
            maximum_age_hours=settings.raw["provider"]["maximum_price_age_hours"],
        )
    if "SPY" not in fresh or fresh["SPY"].status != "usable":
        raise ValueError("Current reference trading sessions unavailable")
    reference = build_reference_sessions({"SPY": fresh["SPY"].usable_bars})
    inputs, scored_securities = {}, []
    for index, security in enumerate(candidates):
        row = next(r for r in rows if r["ticker"] == security.ticker)
        progress(f"Validating {index + 1}/{len(candidates)}: {security.ticker}", flush=True)
        assessment = fresh.get(security.ticker)
        bars = assessment.usable_bars if assessment else ()
        if not bars:
            row["exclusions"].append("Fresh completed price history unavailable")
            errors.append(f"{security.ticker}: price retrieval/freshness needs review")
            continue
        row["price_as_of"] = bars[-1].date.isoformat()
        row["history_sessions"] = len(bars)
        reasons, liquidity = history_checks(bars, reference)
        row["exclusions"].extend(reasons)
        row["median_dollar_volume_20d"] = liquidity
        if row["exclusions"]:
            continue
        try:
            url = f"https://data.sec.gov/submissions/CIK{row['cik']}.json"
            document = client.get_json(url, cache_key=f"submissions-{row['cik']}", ttl_hours=6)
            if str(document.payload.get("cik", "")).zfill(10) != row["cik"]:
                raise ValueError("SEC submission CIK differs from listing identity")
            recent = document.payload.get("filings", {}).get("recent", {})
            row["sec_filings_checked"] = [
                {"form": form, "date": day}
                for form, day in zip(recent.get("form", []), recent.get("filingDate", []))
                if form in {"10-K", "10-K/A", "20-F", "40-F", "6-K", "S-4", "S-4/A", "DEFM14A"}
                or form.startswith(("25", "15-"))
            ]
            annual_confirmed = any(
                form in {"10-K", "10-K/A"}
                and 0 <= (now.date() - date.fromisoformat(day)).days <= 550
                for form, day in zip(recent.get("form", []), recent.get("filingDate", []))
            )
            # High-volume filers can move their 10-K into an SEC history shard.
            if not annual_confirmed and document.payload.get("filings", {}).get("files"):
                history = SecSubmissions.from_settings(settings, client).fetch(
                    identities[normalize_sec_ticker(security.ticker)][0],
                    ticker=security.ticker,
                    since_date=now.date() - timedelta(days=550),
                )
                annual_confirmed = any(f.form in {"10-K", "10-K/A"} for f in history.filings)
                row["sec_history_sources"] = list(history.source_urls)
            reasons, warnings = submission_checks(
                document.payload,
                security.ticker,
                today=now.date(),
                annual_history_confirmed=annual_confirmed,
            )
            row["exclusions"].extend(reasons)
            row["warnings"].extend(warnings)
            row["sec_source"] = {"url": url, "fetched_at": document.fetched_at.isoformat()}
            if reasons:
                continue
            fundamental, warnings = provider.fetch_fundamental(security)
            row["warnings"].extend(warnings)
            if fundamental is None:
                raise ValueError("Fundamental summary unavailable")
            row["fundamentals"] = fundamental.to_dict()
            sector = YAHOO_SECTOR_MAP.get(fundamental.sector or "")
            if sector is None:
                row["exclusions"].append("Sector cannot be validated")
                continue
            row["company"] = fundamental.company or security.company
            row["sector"] = sector
            validity = settings.raw["scoring"]["validity"]
            metrics, warnings = calculate_metrics(
                bars,
                fundamental,
                reference_sessions=reference,
                minimum_debt_to_equity=validity["minimum_debt_to_equity"],
                maximum_return_on_equity=validity["maximum_return_on_equity"],
            )
            metrics, sector_warnings = apply_sector_conventions(metrics, sector)
            row["warnings"].extend(warnings + sector_warnings)
            inputs[security.ticker] = {
                "metrics": metrics,
                "sector": sector,
                "company": row["company"],
                "price_as_of": row["price_as_of"],
            }
            scored_securities.append(Security(security.ticker, row["company"], sector))
        except Exception as exc:  # noqa: BLE001 - provider types vary; record failures explicitly.
            row["exclusions"].append(f"Provider/identity check failed: {exc}")
            errors.append(f"{security.ticker}: provider/identity check failed")
    # One class per issuer in the scoring pool, with preference for an active class.
    seen = set()
    active = {s.ticker for s in settings.universe}
    for row in sorted(rows, key=lambda r: (r["ticker"] not in active, r["ticker"])):
        if row["ticker"] not in inputs:
            continue
        if row["cik"] in seen:
            row["exclusions"].append(
                "Another common share class represents this issuer in the pool"
            )
            inputs.pop(row["ticker"])
        else:
            seen.add(row["cik"])
    scored = score_universe(replace(settings, universe=tuple(scored_securities)), inputs)
    for result in scored:
        row = next(r for r in rows if r["ticker"] == result.ticker)
        row.update(
            score=result.overall_score,
            coverage=result.overall_coverage,
            metrics=result.metrics,
            component_scores=result.component_scores,
        )
        row["warnings"].extend(result.warnings)
        if result.overall_score is None or result.overall_coverage < max(
            0.60, settings.raw["app"]["minimum_overall_coverage"]
        ):
            row["exclusions"].append("Insufficient score coverage (minimum 60% or user threshold)")
    return {
        "candidates": rows,
        "sources": sources,
        "screens": screens,
        "errors": errors,
        "price_warnings": price_warnings,
        "market_date": reference[-1].isoformat(),
        "collected_at": datetime.now(UTC).isoformat(),
        "listing_count": len(listings),
        "pool_size": len(pool),
    }
