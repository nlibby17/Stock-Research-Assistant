"""Bounded discovery policy and immutable proposals, separate from production runs."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from stockrank.config import TICKER_PATTERN, VALID_SECTORS, Settings

POLICY_VERSION = "universe-discovery-v1.1.0"
MAX_UNIVERSE_SIZE = 100
PROFILES = ("best_overall", "diversified")
PROFILE_LABELS = {"best_overall": "Best overall", "diversified": "Diversified"}


@dataclass(frozen=True)
class DiscoveryPolicy:
    profile: str = "diversified"
    target_size: int = 50
    per_sector: int = 10
    enabled: bool = True
    keep_tickers: tuple[str, ...] = ()
    consider_tickers: tuple[str, ...] = ()

    def __post_init__(self):
        if self.profile not in PROFILES:
            raise ValueError("Unknown universe profile")
        if type(self.target_size) is not int or not 11 <= self.target_size <= 100:
            raise ValueError("Universe size must be between 11 and 100")
        if type(self.per_sector) is not int or not 5 <= self.per_sector <= 25:
            raise ValueError("Candidates per sector must be between 5 and 25")
        if type(self.enabled) is not bool:
            raise ValueError("Discovery enabled must be true or false")
        for field, maximum in (("keep_tickers", MAX_UNIVERSE_SIZE), ("consider_tickers", 25)):
            value = getattr(self, field)
            if not isinstance(value, (tuple, list)) or any(not isinstance(t, str) for t in value):
                raise ValueError(f"{field} must be a list of tickers")
            tickers = tuple(sorted({t.strip().upper() for t in value}))
            if len(tickers) > maximum or any(not TICKER_PATTERN.fullmatch(t) for t in tickers):
                raise ValueError(f"{field} requires valid tickers (maximum {maximum})")
            object.__setattr__(self, field, tickers)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def context_key(settings: Settings, policy: DiscoveryPolicy) -> str:
    return digest(
        {
            "policy_version": POLICY_VERSION,
            "policy": asdict(policy),
            "universe": [asdict(s) for s in settings.universe],
            "scoring": settings.raw["scoring"],
            "provider": settings.raw["provider"],
            "sec": settings.raw["sec"],
            "coverage": settings.raw["app"]["minimum_overall_coverage"],
        }
    )


def discovery_due(last_success: str | None, now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("Discovery clock requires a timezone")
    if last_success is None:
        return True
    previous = datetime.fromisoformat(last_success)
    if previous.tzinfo is None or previous > now:
        raise ValueError("Invalid discovery timestamp; inspect universe status")
    return now - previous >= timedelta(days=7)


def select_members(rows: list[dict], profile: str, size: int) -> list[dict]:
    """Select by pool-relative score; diversified fills least-represented sectors first."""
    if profile not in PROFILES:
        raise ValueError("Unknown universe profile")
    ranked = sorted(
        (r for r in rows if not r["exclusions"] and r.get("score") is not None),
        key=lambda r: (-r["score"], r["ticker"]),
    )
    chosen, counts, issuers = [], Counter(), set()
    while ranked and len(chosen) < size:
        available = [r for r in ranked if r["cik"] not in issuers]
        if not available:
            break
        if profile == "diversified":
            row = min(available, key=lambda r: (counts[r["sector"]], -r["score"], r["ticker"]))
        else:
            row = available[0]
        chosen.append(row)
        counts[row["sector"]] += 1
        issuers.add(row["cik"])
        ranked.remove(row)
    return chosen


def build_proposal(
    settings: Settings, policy: DiscoveryPolicy, evidence: dict, *, now: datetime, preview: bool
) -> dict:
    rows = evidence["candidates"]
    if len({r["ticker"] for r in rows}) != len(rows):
        raise ValueError("Discovery contains duplicate tickers")
    for row in rows:
        if not row["exclusions"] and (
            row["sector"] not in VALID_SECTORS
            or not row.get("cik")
            or row.get("score") is None
            or not math.isfinite(row["score"])
        ):
            raise ValueError("Eligible candidate has incomplete identity or score")
    active = {s.ticker for s in settings.universe}
    missing = active - {r["ticker"] for r in rows}
    if missing:
        raise ValueError("Discovery did not assess active members: " + ", ".join(sorted(missing)))
    profiles = {}
    for profile in PROFILES:
        selected = select_members(rows, profile, policy.target_size)
        tickers = {r["ticker"] for r in selected}
        sectors = Counter(r["sector"] for r in selected)
        blockers = list(evidence.get("errors", []))
        if len(selected) != policy.target_size:
            blockers.append(
                f"Only {len(selected)} eligible issuers for {policy.target_size} places"
            )
        if profile == "diversified" and set(sectors) != VALID_SECTORS:
            blockers.append("Diversified requires eligible candidates in all 11 sectors")
        removals = []
        for ticker in sorted(active - tickers):
            row = next(r for r in rows if r["ticker"] == ticker)
            reasons = row["exclusions"] or [
                "Outside selected places under this profile (including one share class per issuer)"
            ]
            removals.append(
                {
                    "ticker": ticker,
                    "reasons": reasons,
                    "score": row.get("score"),
                    "coverage": row.get("coverage"),
                    "sector": row["sector"],
                }
            )
        profiles[profile] = {
            "members": [{k: r[k] for k in ("ticker", "company", "sector")} for r in selected],
            "additions": sorted(tickers - active),
            "removals": removals,
            "retained": sorted(active & tickers),
            "sector_counts": dict(sorted(sectors.items())),
            "blockers": blockers,
        }
        # A user's protected current members remain visible even if discovery excludes them.
        protected = set(policy.keep_tickers) & active
        profiles[profile] = personalize_selection(
            {
                "base_members": [asdict(s) for s in settings.universe],
                "evidence": evidence,
                "profiles": {profile: profiles[profile]},
            },
            profile,
            protect=protected,
        )
    proposal = {
        "schema": 1,
        "policy_version": POLICY_VERSION,
        "created_at": now.astimezone(UTC).isoformat(),
        "preview": preview,
        "context": context_key(settings, policy),
        "policy": asdict(policy),
        "base_universe": settings.raw["universe"]["name"],
        "base_members": [asdict(s) for s in settings.universe],
        "model_version": settings.model_version,
        "scoring": settings.raw["scoring"],
        "profiles": profiles,
        "evidence": evidence,
        "warnings": [
            "Scores are relative to this bounded screened pool, not the entire market or a forecast.",
            "Pool: largest companies by market cap, up to the per-sector limit, plus active members.",
            "Diversified balances company counts across sectors, not investment weights or risk.",
            "No persistence gate: this is a single dated screen; review turnover before approval.",
            "Corporate-action screening is limited. Review mergers, delistings and identity warnings before approval.",
            "Approval changes future membership only. Daily scores will be recalculated against that membership.",
        ],
    }
    proposal["id"] = digest(proposal)
    return proposal


def verify_proposal(proposal: dict) -> None:
    content = {k: v for k, v in proposal.items() if k != "id"}
    if proposal.get("schema") != 1 or proposal.get("policy_version") not in {
        POLICY_VERSION,
        "universe-discovery-v1.0.0",
    }:
        raise ValueError("Unsupported proposal version; create a new preview")
    if proposal.get("id") != digest(content):
        raise ValueError("Proposal changed after generation; create a new preview")


def personalize_selection(proposal, profile, *, protect=(), skip=(), include=(), remove=()) -> dict:
    """Explicit user edits grow/shrink the reviewed list; never silently displace another stock."""
    protect, skip, include = set(protect), set(skip), set(include)
    baseline = proposal["profiles"][profile]
    active = {m["ticker"]: m for m in proposal["base_members"]}
    evidence = {r["ticker"]: r for r in proposal["evidence"]["candidates"]}
    if not protect <= active.keys():
        raise ValueError(
            "Only current members can be protected; nominate new tickers for screening first"
        )
    if not skip <= set(baseline["additions"]):
        raise ValueError("Only proposed additions can be skipped")
    for ticker in include:
        row = evidence.get(ticker)
        if row is None or row["exclusions"] or row.get("score") is None:
            raise ValueError(f"{ticker}: additional members must pass discovery checks first")
    if include & skip:
        raise ValueError("A ticker cannot be both included and skipped")
    members = {m["ticker"]: dict(m) for m in baseline["members"] if m["ticker"] not in skip}
    members.update((t, dict(active[t])) for t in sorted(protect))
    members.update(
        (t, {k: evidence[t][k] for k in ("ticker", "company", "sector")}) for t in sorted(include)
    )
    remove = set(remove)
    if not remove <= members.keys():
        raise ValueError("Only selected members can be removed")
    if remove & protect:
        raise ValueError("Uncheck Keep in future reviews before removing a protected stock")
    members = {t: m for t, m in members.items() if t not in remove}
    warnings = []
    for ticker in sorted(protect):
        reasons = evidence.get(ticker, {}).get("exclusions", [])
        if reasons:
            warnings.append(f"{ticker} is kept by your override despite: " + "; ".join(reasons))
    if profile == "diversified" and {m["sector"] for m in members.values()} != VALID_SECTORS:
        warnings.append("Your edited selection no longer covers all 11 sectors.")
    return {
        **baseline,
        "members": list(members.values()),
        "additions": sorted(members.keys() - active.keys()),
        "retained": sorted(members.keys() & active.keys()),
        "removals": [
            {
                "ticker": t,
                "sector": active[t]["sector"],
                "score": evidence[t].get("score"),
                "reasons": next(
                    (r["reasons"] for r in baseline["removals"] if r["ticker"] == t),
                    ["Removed by your selection"],
                ),
            }
            for t in sorted(active.keys() - members.keys())
        ],
        "sector_counts": dict(sorted(Counter(m["sector"] for m in members.values()).items())),
        "protected": sorted(protect),
        "skipped": sorted(skip),
        "included": sorted(include),
        "manual_removals": sorted(remove),
        "override_warnings": warnings,
    }


def validate_selection(selected):
    if len(selected["members"]) > MAX_UNIVERSE_SIZE:
        raise ValueError(
            f"Your list is full: {len(selected['members'])} stocks. The hard cap is {MAX_UNIVERSE_SIZE}. Remove {len(selected['members']) - MAX_UNIVERSE_SIZE} stocks before approval."
        )
    if len(selected["members"]) < 10:
        raise ValueError("Keep at least 10 stocks in your universe")


def suggested_trim(proposal, profile, selected):
    """Offer a bounded trim for confirmation, preserving all protected members."""
    rows = {r["ticker"]: r for r in proposal["evidence"]["candidates"]}
    remaining = {m["ticker"]: m for m in selected["members"]}
    trimmed = []
    while len(remaining) > MAX_UNIVERSE_SIZE:
        counts = Counter(m["sector"] for m in remaining.values())
        available = [t for t in remaining if t not in selected["protected"]]
        if not available:
            raise ValueError("Unprotect some stocks before requesting a suggested trim")
        ticker = min(
            available,
            key=lambda t: (
                -counts[remaining[t]["sector"]] if profile == "diversified" else 0,
                rows[t].get("score") if rows[t].get("score") is not None else -1,
                t,
            ),
        )
        trimmed.append(ticker)
        del remaining[ticker]
    return trimmed


def atomic_json(path: Path, value: dict) -> None:
    from uuid import uuid4

    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        staged.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
