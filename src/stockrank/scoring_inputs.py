"""Shared, static contract for normalized inputs to the scoring engine."""

from typing import NotRequired, TypedDict


class ScoringInput(TypedDict):
    """Metrics are required; absent metadata retains scoring's existing defaults.

    Metric names remain configuration-driven. Missing metric keys and explicit
    None values both represent unavailable data. This is a regular dictionary at
    runtime, not a replacement for provider validation or freshness checks.
    """

    metrics: dict[str, float | None]
    company: NotRequired[str | None]
    sector: NotRequired[str | None]
    price_as_of: NotRequired[str | None]
    warnings: NotRequired[list[str]]
