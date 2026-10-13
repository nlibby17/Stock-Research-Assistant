"""Stable public entry point for the SEC adapter.

Implementation lives in focused modules; existing callers can keep these imports.
"""

from stockrank.data.sec_companyfacts import SEC_COMPANYFACTS_BASE_URL, SecCompanyFacts
from stockrank.data.sec_configuration import (
    XBRL_MEMBER_PATTERN,
    load_sec_concept_specs,
    load_sec_entity_overrides,
    normalize_sec_ticker,
    validate_sec_configuration,
)
from stockrank.data.sec_filings import (
    ACCESSION_PATTERN,
    SEC_IDENTITY_URL,
    SEC_SUBMISSIONS_BASE_URL,
    SUBMISSION_FILE_PATTERN,
    SecIdentityDirectory,
    SecSubmissions,
)
from stockrank.data.sec_transport import (
    EMAIL_PATTERN,
    SEC_ALLOWED_HOSTS,
    SEC_CACHE_SCHEMA_VERSION,
    SEC_CLOCK_SKEW_TOLERANCE,
    SEC_RETRYABLE_STATUS_CODES,
    SecClient,
    validate_sec_user_agent,
)
from stockrank.data.sec_types import (
    SecCompanyFactsSnapshot,
    SecCompanyIdentity,
    SecConceptSpec,
    SecConfigurationError,
    SecError,
    SecIdentitySnapshot,
    SecJsonDocument,
    SecPayloadError,
    SecRequestError,
    SecSubmissionSnapshot,
)

__all__ = [
    "ACCESSION_PATTERN",
    "EMAIL_PATTERN",
    "SEC_ALLOWED_HOSTS",
    "SEC_CACHE_SCHEMA_VERSION",
    "SEC_CLOCK_SKEW_TOLERANCE",
    "SEC_COMPANYFACTS_BASE_URL",
    "SEC_IDENTITY_URL",
    "SEC_RETRYABLE_STATUS_CODES",
    "SEC_SUBMISSIONS_BASE_URL",
    "SUBMISSION_FILE_PATTERN",
    "XBRL_MEMBER_PATTERN",
    "SecClient",
    "SecCompanyFacts",
    "SecCompanyFactsSnapshot",
    "SecCompanyIdentity",
    "SecConceptSpec",
    "SecConfigurationError",
    "SecError",
    "SecIdentityDirectory",
    "SecIdentitySnapshot",
    "SecJsonDocument",
    "SecPayloadError",
    "SecRequestError",
    "SecSubmissionSnapshot",
    "SecSubmissions",
    "load_sec_concept_specs",
    "load_sec_entity_overrides",
    "normalize_sec_ticker",
    "validate_sec_configuration",
    "validate_sec_user_agent",
]
