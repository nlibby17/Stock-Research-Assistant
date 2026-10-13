"""SEC Company Facts normalization and point-in-time selection."""

from __future__ import annotations

from datetime import UTC, date, datetime
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from stockrank.data.sec_configuration import load_sec_concept_specs, normalize_sec_ticker
from stockrank.data.sec_filings import ACCESSION_PATTERN, _parse_date
from stockrank.data.sec_transport import SecClient
from stockrank.data.sec_types import (
    SecCompanyFactsSnapshot,
    SecCompanyIdentity,
    SecConceptSpec,
    SecConfigurationError,
    SecPayloadError,
)
from stockrank.models import SecCompanyFact, SecFiling

if TYPE_CHECKING:
    from stockrank.config import Settings


SEC_COMPANYFACTS_BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts"


class SecCompanyFacts:
    """Normalize an explicit allowlist of entity-wide SEC XBRL facts."""

    def __init__(
        self,
        client: SecClient,
        *,
        concept_specs: tuple[SecConceptSpec, ...],
        cache_ttl_hours: float = 6.0,
        forms: tuple[str, ...] = ("10-K", "10-K/A", "10-Q", "10-Q/A"),
    ):
        if cache_ttl_hours <= 0:
            raise SecConfigurationError("SEC companyfacts_cache_ttl_hours must be positive.")
        if not concept_specs:
            raise SecConfigurationError("SEC Company Facts requires a concept allowlist.")
        normalized_forms = tuple(dict.fromkeys(form.strip().upper() for form in forms if form))
        if not normalized_forms:
            raise SecConfigurationError("SEC filing_forms cannot be empty.")
        canonical_names = [spec.canonical_name for spec in concept_specs]
        if len(set(canonical_names)) != len(canonical_names):
            raise SecConfigurationError("SEC Company Facts canonical names must be unique.")
        self.client = client
        self.concept_specs = concept_specs
        self.cache_ttl_hours = cache_ttl_hours
        self.forms = normalized_forms

    @classmethod
    def from_settings(cls, settings: Settings, client: SecClient | None = None) -> SecCompanyFacts:
        config = settings.raw.get("sec", {})
        return cls(
            client or SecClient.from_settings(settings),
            concept_specs=load_sec_concept_specs(settings),
            cache_ttl_hours=float(config.get("companyfacts_cache_ttl_hours", 6.0)),
            forms=tuple(
                str(form)
                for form in config.get("filing_forms", ("10-K", "10-K/A", "10-Q", "10-Q/A"))
            ),
        )

    def fetch(
        self,
        identity: SecCompanyIdentity,
        *,
        ticker: str,
        since_date: date,
        filings: tuple[SecFiling, ...] = (),
        force: bool = False,
    ) -> SecCompanyFactsSnapshot:
        url = f"{SEC_COMPANYFACTS_BASE_URL}/CIK{identity.cik}.json"
        document = self.client.get_json(
            url,
            cache_key=f"companyfacts-{identity.cik}",
            ttl_hours=self.cache_ttl_hours,
            force=force,
        )
        payload = document.payload
        if not isinstance(payload, dict):
            raise SecPayloadError("SEC Company Facts payload must be a JSON object.")
        payload_cik = str(payload.get("cik", "")).strip()
        if not payload_cik.isdigit() or payload_cik.zfill(10) != identity.cik:
            raise SecPayloadError("SEC Company Facts payload CIK does not match the request.")
        facts_node = payload.get("facts")
        if not isinstance(facts_node, dict):
            raise SecPayloadError("SEC Company Facts payload is missing facts.")
        company_name = str(payload.get("entityName") or identity.name).strip()
        filing_index = {
            filing.accession_number: filing for filing in filings if filing.cik == identity.cik
        }
        normalized: dict[tuple[Any, ...], SecCompanyFact] = {}
        unmatched_accessions: set[str] = set()

        for spec in self.concept_specs:
            for priority, (taxonomy, concept) in enumerate(spec.members):
                taxonomy_node = facts_node.get(taxonomy)
                if taxonomy_node is None:
                    continue
                if not isinstance(taxonomy_node, dict):
                    raise SecPayloadError(
                        f"SEC Company Facts taxonomy {taxonomy} must be an object."
                    )
                concept_node = taxonomy_node.get(concept)
                if concept_node is None:
                    continue
                if not isinstance(concept_node, dict):
                    raise SecPayloadError(
                        f"SEC Company Facts concept {taxonomy}:{concept} must be an object."
                    )
                units_node = concept_node.get("units")
                if not isinstance(units_node, dict):
                    raise SecPayloadError(
                        f"SEC Company Facts concept {taxonomy}:{concept} is missing units."
                    )
                label = str(concept_node.get("label") or concept).strip()
                description = str(concept_node.get("description") or "").strip()
                for unit in spec.units:
                    rows = units_node.get(unit)
                    if rows is None:
                        continue
                    if not isinstance(rows, list):
                        raise SecPayloadError(
                            f"SEC Company Facts unit {taxonomy}:{concept}/{unit} must be an array."
                        )
                    for record in rows:
                        fact = self._parse_record(
                            record,
                            identity=identity,
                            ticker=ticker,
                            company_name=company_name,
                            spec=spec,
                            taxonomy=taxonomy,
                            concept=concept,
                            concept_priority=priority,
                            label=label,
                            description=description,
                            unit=unit,
                            source_url=url,
                            fetched_at=document.fetched_at,
                            since_date=since_date,
                            filing_index=filing_index,
                        )
                        if fact is None:
                            continue
                        if fact.accession_number not in filing_index:
                            unmatched_accessions.add(fact.accession_number)
                        key = (
                            fact.canonical_name,
                            fact.taxonomy,
                            fact.concept,
                            fact.unit,
                            fact.start_date,
                            fact.end_date,
                            fact.accession_number,
                            fact.form,
                        )
                        existing = normalized.get(key)
                        if existing is not None and existing.value != fact.value:
                            raise SecPayloadError(
                                "SEC Company Facts contains conflicting values for an "
                                f"identical context: {taxonomy}:{concept} "
                                f"{fact.accession_number}"
                            )
                        normalized.setdefault(key, fact)

        values = tuple(
            sorted(
                normalized.values(),
                key=lambda fact: (
                    fact.end_date,
                    fact.start_date or fact.end_date,
                    fact.availability_date,
                    fact.accession_number,
                    fact.canonical_name,
                    -fact.concept_priority,
                ),
                reverse=True,
            )
        )
        present = tuple(
            spec.canonical_name
            for spec in self.concept_specs
            if any(fact.canonical_name == spec.canonical_name for fact in values)
        )
        return SecCompanyFactsSnapshot(
            cik=identity.cik,
            ticker=normalize_sec_ticker(ticker),
            company_name=company_name,
            facts=values,
            source_url=url,
            fetched_at=document.fetched_at,
            cache_hit=document.cache_hit,
            stale=document.stale,
            configured_concepts=tuple(spec.canonical_name for spec in self.concept_specs),
            present_concepts=present,
            unmatched_accessions=len(unmatched_accessions),
        )

    def _parse_record(
        self,
        record: Any,
        *,
        identity: SecCompanyIdentity,
        ticker: str,
        company_name: str,
        spec: SecConceptSpec,
        taxonomy: str,
        concept: str,
        concept_priority: int,
        label: str,
        description: str,
        unit: str,
        source_url: str,
        fetched_at: datetime,
        since_date: date,
        filing_index: dict[str, SecFiling],
    ) -> SecCompanyFact | None:
        if not isinstance(record, dict):
            raise SecPayloadError("SEC Company Facts contains a malformed fact record.")
        form = str(record.get("form") or "").strip().upper()
        if form not in self.forms:
            return None
        filed_date = _parse_date(record.get("filed"), field="filed", required=True)
        assert filed_date is not None
        if filed_date < since_date:
            return None
        accession_number = str(record.get("accn") or "").strip()
        if not ACCESSION_PATTERN.fullmatch(accession_number):
            raise SecPayloadError(
                f"SEC Company Facts contains invalid accession number: {accession_number}"
            )
        end_date = _parse_date(record.get("end"), field="end", required=True)
        assert end_date is not None
        start_date = _parse_date(record.get("start"), field="start", required=False)
        if spec.period_type == "duration" and start_date is None:
            raise SecPayloadError(
                f"SEC duration fact {taxonomy}:{concept} is missing a start date."
            )
        if spec.period_type == "instant" and start_date is not None:
            raise SecPayloadError(
                f"SEC instant fact {taxonomy}:{concept} unexpectedly has a start date."
            )
        raw_value = record.get("val")
        if isinstance(raw_value, bool) or raw_value is None:
            raise SecPayloadError(f"SEC Company Facts {taxonomy}:{concept} has invalid value.")
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError) as exc:
            raise SecPayloadError(
                f"SEC Company Facts {taxonomy}:{concept} has nonnumeric value."
            ) from exc
        if not value.is_finite():
            raise SecPayloadError(f"SEC Company Facts {taxonomy}:{concept} has non-finite value.")
        raw_fiscal_year = record.get("fy")
        try:
            fiscal_year = int(raw_fiscal_year) if raw_fiscal_year not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise SecPayloadError(
                f"SEC Company Facts {taxonomy}:{concept} has invalid fiscal year."
            ) from exc
        fiscal_period = str(record.get("fp") or "").strip().upper() or None
        frame = str(record.get("frame") or "").strip() or None
        filing = filing_index.get(accession_number)
        return SecCompanyFact(
            cik=identity.cik,
            ticker=normalize_sec_ticker(ticker),
            company_name=company_name,
            canonical_name=spec.canonical_name,
            taxonomy=taxonomy,
            concept=concept,
            concept_priority=concept_priority,
            label=label,
            description=description,
            period_type=spec.period_type,
            unit=unit,
            value=value,
            start_date=start_date,
            end_date=end_date,
            accession_number=accession_number,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            form=form,
            filed_date=filed_date,
            frame=frame,
            accepted_at=filing.accepted_at if filing else None,
            availability_date=filing.availability_date if filing else filed_date,
            availability_precision=filing.availability_precision if filing else "date",
            source_url=source_url,
            fetched_at=fetched_at,
        )

    @staticmethod
    def effective_facts(
        facts: tuple[SecCompanyFact, ...], *, available_at: datetime | None = None
    ) -> tuple[SecCompanyFact, ...]:
        if available_at is not None and available_at.tzinfo is None:
            raise ValueError("SEC fact available_at cutoff must include a timezone.")
        selected: dict[tuple[str, str, date | None, date], SecCompanyFact] = {}
        for fact in facts:
            if available_at is not None:
                if fact.accepted_at and fact.accepted_at > available_at:
                    continue
                if not fact.accepted_at and fact.availability_date > available_at.date():
                    continue
            key = (fact.canonical_name, fact.unit, fact.start_date, fact.end_date)
            current = selected.get(key)
            if current is None or SecCompanyFacts._selection_order(fact) > (
                SecCompanyFacts._selection_order(current)
            ):
                selected[key] = fact
        return tuple(
            sorted(
                selected.values(),
                key=lambda fact: (
                    fact.end_date,
                    fact.start_date or fact.end_date,
                    fact.canonical_name,
                ),
                reverse=True,
            )
        )

    @staticmethod
    def _selection_order(fact: SecCompanyFact) -> tuple[Any, ...]:
        available = fact.accepted_at or datetime.combine(
            fact.availability_date, datetime_time.min, tzinfo=UTC
        )
        return (available, fact.accession_number, -fact.concept_priority)
