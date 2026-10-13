"""SEC issuer identities, submissions, and filing availability."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from stockrank.data.sec_configuration import normalize_sec_ticker
from stockrank.data.sec_transport import SecClient
from stockrank.data.sec_types import (
    SecCompanyIdentity,
    SecConfigurationError,
    SecIdentitySnapshot,
    SecPayloadError,
    SecSubmissionSnapshot,
)
from stockrank.models import SecFiling

if TYPE_CHECKING:
    from stockrank.config import Settings


SEC_IDENTITY_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
ACCESSION_PATTERN = re.compile(r"^\d{10}-\d{2}-\d{6}$")
SUBMISSION_FILE_PATTERN = re.compile(r"^CIK\d{10}-submissions-\d{3}\.json$")
SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"


class SecIdentityDirectory:
    def __init__(
        self,
        client: SecClient,
        *,
        url: str = SEC_IDENTITY_URL,
        cache_ttl_hours: float = 24.0,
    ):
        self.client = client
        self.url = url
        if cache_ttl_hours <= 0:
            raise SecConfigurationError("SEC identity_cache_ttl_hours must be positive.")
        self.cache_ttl_hours = cache_ttl_hours

    @classmethod
    def from_settings(
        cls, settings: Settings, client: SecClient | None = None
    ) -> SecIdentityDirectory:
        config = settings.raw.get("sec", {})
        return cls(
            client or SecClient.from_settings(settings),
            url=str(config.get("identity_url", SEC_IDENTITY_URL)),
            cache_ttl_hours=float(config.get("identity_cache_ttl_hours", 24.0)),
        )

    def fetch(self, *, force: bool = False) -> SecIdentitySnapshot:
        document = self.client.get_json(
            self.url,
            cache_key="company-tickers-exchange",
            ttl_hours=self.cache_ttl_hours,
            force=force,
        )
        identities = self._parse(document.payload)
        return SecIdentitySnapshot(
            identities=identities,
            source_url=document.source_url,
            fetched_at=document.fetched_at,
            cache_hit=document.cache_hit,
            stale=document.stale,
        )

    @staticmethod
    def _parse(payload: Any) -> tuple[SecCompanyIdentity, ...]:
        if not isinstance(payload, dict):
            raise SecPayloadError("SEC identity payload must be a JSON object.")
        fields = payload.get("fields")
        rows = payload.get("data")
        required = ("cik", "name", "ticker", "exchange")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise SecPayloadError("SEC identity payload is missing fields or data arrays.")
        try:
            positions = {field: fields.index(field) for field in required}
        except ValueError as exc:
            raise SecPayloadError(f"SEC identity payload is missing required field: {exc}") from exc

        identities: list[SecCompanyIdentity] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < len(fields):
                raise SecPayloadError("SEC identity payload contains a malformed row.")
            cik_value = str(row[positions["cik"]]).strip()
            ticker = normalize_sec_ticker(str(row[positions["ticker"]]))
            if not cik_value.isdigit() or len(cik_value) > 10 or not ticker:
                raise SecPayloadError("SEC identity payload contains an invalid CIK or ticker.")
            identities.append(
                SecCompanyIdentity(
                    cik=cik_value.zfill(10),
                    name=str(row[positions["name"]]).strip(),
                    ticker=ticker,
                    exchange=str(row[positions["exchange"]]).strip(),
                )
            )
        if not identities:
            raise SecPayloadError("SEC identity payload contains no identities.")
        return tuple(identities)

    @staticmethod
    def index_by_ticker(
        identities: tuple[SecCompanyIdentity, ...],
    ) -> dict[str, SecCompanyIdentity]:
        return {identity.ticker: identity for identity in identities}

    @classmethod
    def resolve(
        cls, ticker: str, identities: tuple[SecCompanyIdentity, ...]
    ) -> SecCompanyIdentity | None:
        return cls.index_by_ticker(identities).get(normalize_sec_ticker(ticker))


def _parse_date(value: Any, *, field: str, required: bool) -> date | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise SecPayloadError(f"SEC filing is missing required {field}.")
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SecPayloadError(f"SEC filing contains invalid {field}: {text}") from exc


def _parse_acceptance_datetime(value: Any) -> tuple[str | None, datetime | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, None
    # SEC defines the acceptance clock as Eastern time. The submissions feed
    # commonly appends "Z" to that wall-clock value, so preserve the raw string
    # but localize the clock to America/New_York before converting to true UTC.
    normalized = raw.removesuffix("Z")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SecPayloadError(f"SEC filing contains invalid acceptanceDateTime: {raw}") from exc
    if raw.endswith("Z") or parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return raw, parsed.astimezone(UTC)


def _filing_urls(
    cik: str, accession_number: str, primary_document: str | None
) -> tuple[str, str | None]:
    accession_compact = accession_number.replace("-", "")
    archive_cik = str(int(cik))
    directory = f"https://www.sec.gov/Archives/edgar/data/{archive_cik}/{accession_compact}"
    index_url = f"{directory}/{accession_number}-index.html"
    if not primary_document:
        return index_url, None
    if Path(primary_document).name != primary_document or ".." in primary_document:
        raise SecPayloadError("SEC filing contains an unsafe primaryDocument path.")
    return index_url, f"{directory}/{quote(primary_document)}"


class SecSubmissions:
    REQUIRED_COLUMNS = ("accessionNumber", "filingDate", "form")

    def __init__(
        self,
        client: SecClient,
        *,
        cache_ttl_hours: float = 6.0,
        forms: tuple[str, ...] = ("10-K", "10-K/A", "10-Q", "10-Q/A"),
    ):
        if cache_ttl_hours <= 0:
            raise SecConfigurationError("SEC submissions_cache_ttl_hours must be positive.")
        normalized_forms = tuple(dict.fromkeys(form.strip().upper() for form in forms if form))
        if not normalized_forms:
            raise SecConfigurationError("SEC filing_forms cannot be empty.")
        self.client = client
        self.cache_ttl_hours = cache_ttl_hours
        self.forms = normalized_forms

    @classmethod
    def from_settings(cls, settings: Settings, client: SecClient | None = None) -> SecSubmissions:
        config = settings.raw.get("sec", {})
        return cls(
            client or SecClient.from_settings(settings),
            cache_ttl_hours=float(config.get("submissions_cache_ttl_hours", 6.0)),
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
        force: bool = False,
    ) -> SecSubmissionSnapshot:
        root_url = f"{SEC_SUBMISSIONS_BASE_URL}/CIK{identity.cik}.json"
        root_document = self.client.get_json(
            root_url,
            cache_key=f"submissions-{identity.cik}",
            ttl_hours=self.cache_ttl_hours,
            force=force,
        )
        if not isinstance(root_document.payload, dict):
            raise SecPayloadError("SEC submissions payload must be a JSON object.")
        payload_cik = str(root_document.payload.get("cik", "")).strip()
        if not payload_cik.isdigit() or payload_cik.zfill(10) != identity.cik:
            raise SecPayloadError("SEC submissions payload CIK does not match requested identity.")
        company_name = str(root_document.payload.get("name") or identity.name).strip()
        filings_node = root_document.payload.get("filings")
        if not isinstance(filings_node, dict):
            raise SecPayloadError("SEC submissions payload is missing the filings object.")
        recent = filings_node.get("recent")
        if not isinstance(recent, dict):
            raise SecPayloadError("SEC submissions payload is missing filings.recent.")

        filings_by_accession = {
            filing.accession_number: filing
            for filing in self._parse_columnar(
                recent,
                identity=identity,
                ticker=ticker,
                company_name=company_name,
                source_url=root_url,
                fetched_at=root_document.fetched_at,
                since_date=since_date,
            )
        }
        documents = [root_document]
        files = filings_node.get("files", [])
        if files is None:
            files = []
        if not isinstance(files, list):
            raise SecPayloadError("SEC submissions filings.files must be an array.")
        for file_record in files:
            if not self._history_file_intersects(file_record, since_date):
                continue
            file_name = str(file_record.get("name", "")).strip()
            if not SUBMISSION_FILE_PATTERN.fullmatch(file_name):
                raise SecPayloadError(f"SEC submissions contains unsafe history file: {file_name}")
            history_url = f"{SEC_SUBMISSIONS_BASE_URL}/{file_name}"
            history_document = self.client.get_json(
                history_url,
                cache_key=f"submissions-history-{file_name.removesuffix('.json')}",
                ttl_hours=self.cache_ttl_hours,
                force=force,
            )
            if not isinstance(history_document.payload, dict):
                raise SecPayloadError("SEC historical submissions payload must be an object.")
            history_node = history_document.payload
            if isinstance(history_node.get("filings"), dict):
                history_node = history_node["filings"].get("recent", history_node)
            for filing in self._parse_columnar(
                history_node,
                identity=identity,
                ticker=ticker,
                company_name=company_name,
                source_url=history_url,
                fetched_at=history_document.fetched_at,
                since_date=since_date,
            ):
                filings_by_accession.setdefault(filing.accession_number, filing)
            documents.append(history_document)

        filings = tuple(
            sorted(
                filings_by_accession.values(),
                key=lambda value: (
                    value.accepted_at
                    or datetime.combine(value.filing_date, datetime_time.min, tzinfo=UTC),
                    value.accession_number,
                ),
                reverse=True,
            )
        )
        return SecSubmissionSnapshot(
            cik=identity.cik,
            ticker=normalize_sec_ticker(ticker),
            company_name=company_name,
            filings=filings,
            source_urls=tuple(document.source_url for document in documents),
            fetched_at=max(document.fetched_at for document in documents),
            cache_hits=sum(document.cache_hit for document in documents),
            documents_checked=len(documents),
            stale=any(document.stale for document in documents),
        )

    def _parse_columnar(
        self,
        payload: dict[str, Any],
        *,
        identity: SecCompanyIdentity,
        ticker: str,
        company_name: str,
        source_url: str,
        fetched_at: datetime,
        since_date: date,
    ) -> tuple[SecFiling, ...]:
        for column in self.REQUIRED_COLUMNS:
            if not isinstance(payload.get(column), list):
                raise SecPayloadError(f"SEC submissions payload is missing {column} array.")
        row_count = len(payload["accessionNumber"])
        if any(len(payload[column]) != row_count for column in self.REQUIRED_COLUMNS):
            raise SecPayloadError("SEC submissions required columns have inconsistent lengths.")

        filings: list[SecFiling] = []
        for index in range(row_count):
            form = self._column_value(payload, "form", index).upper()
            if form not in self.forms:
                continue
            filing_date = _parse_date(
                self._column_value(payload, "filingDate", index),
                field="filingDate",
                required=True,
            )
            assert filing_date is not None
            if filing_date < since_date:
                continue
            accession_number = self._column_value(payload, "accessionNumber", index)
            if not ACCESSION_PATTERN.fullmatch(accession_number):
                raise SecPayloadError(
                    f"SEC filing contains invalid accessionNumber: {accession_number}"
                )
            report_date = _parse_date(
                self._column_value(payload, "reportDate", index),
                field="reportDate",
                required=False,
            )
            acceptance_raw, accepted_at = _parse_acceptance_datetime(
                self._column_value(payload, "acceptanceDateTime", index)
            )
            primary_document = self._column_value(payload, "primaryDocument", index) or None
            filing_index_url, primary_document_url = _filing_urls(
                identity.cik, accession_number, primary_document
            )
            filings.append(
                SecFiling(
                    cik=identity.cik,
                    ticker=normalize_sec_ticker(ticker),
                    company_name=company_name,
                    accession_number=accession_number,
                    form=form,
                    base_form=form.removesuffix("/A"),
                    is_amendment=form.endswith("/A"),
                    filing_date=filing_date,
                    report_date=report_date,
                    acceptance_datetime=acceptance_raw,
                    accepted_at=accepted_at,
                    availability_date=accepted_at.date() if accepted_at else filing_date,
                    availability_precision="timestamp" if accepted_at else "date",
                    primary_document=primary_document,
                    filing_index_url=filing_index_url,
                    primary_document_url=primary_document_url,
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
            )
        return tuple(filings)

    @staticmethod
    def _column_value(payload: dict[str, Any], column: str, index: int) -> str:
        values = payload.get(column)
        if not isinstance(values, list) or index >= len(values) or values[index] is None:
            return ""
        return str(values[index]).strip()

    @staticmethod
    def _history_file_intersects(file_record: Any, since_date: date) -> bool:
        if not isinstance(file_record, dict):
            raise SecPayloadError("SEC submissions contains a malformed history file record.")
        filing_to = _parse_date(
            file_record.get("filingTo"), field="filings.files.filingTo", required=False
        )
        return filing_to is None or filing_to >= since_date

    @staticmethod
    def effective_filings(
        filings: tuple[SecFiling, ...], *, available_at: datetime | None = None
    ) -> tuple[SecFiling, ...]:
        if available_at is not None and available_at.tzinfo is None:
            raise ValueError("SEC filing available_at cutoff must include a timezone.")
        selected: dict[tuple[str, date], SecFiling] = {}
        for filing in filings:
            if available_at is not None:
                if filing.accepted_at and filing.accepted_at > available_at:
                    continue
                if not filing.accepted_at and filing.availability_date > available_at.date():
                    continue
            period = filing.report_date or filing.filing_date
            key = (filing.base_form, period)
            current = selected.get(key)
            filing_order = (
                filing.accepted_at
                or datetime.combine(filing.filing_date, datetime_time.min, tzinfo=UTC),
                filing.accession_number,
            )
            current_order = (
                (
                    (
                        current.accepted_at
                        or datetime.combine(current.filing_date, datetime_time.min, tzinfo=UTC)
                    ),
                    current.accession_number,
                )
                if current
                else None
            )
            if current_order is None or filing_order > current_order:
                selected[key] = filing
        return tuple(
            sorted(
                selected.values(),
                key=lambda value: (
                    value.accepted_at
                    or datetime.combine(value.filing_date, datetime_time.min, tzinfo=UTC),
                    value.accession_number,
                ),
                reverse=True,
            )
        )
