"""The command and calculation layers retain one shared SEC public API."""

from stockrank.data import (
    sec,
    sec_companyfacts,
    sec_configuration,
    sec_filings,
    sec_transport,
    sec_types,
)


def test_public_sec_imports_share_the_implementation_objects():
    assert sec.SecClient is sec_transport.SecClient
    assert sec.SecIdentityDirectory is sec_filings.SecIdentityDirectory
    assert sec.SecSubmissions is sec_filings.SecSubmissions
    assert sec.SecCompanyFacts is sec_companyfacts.SecCompanyFacts
    assert sec.load_sec_concept_specs is sec_configuration.load_sec_concept_specs
    assert sec.validate_sec_configuration is sec_configuration.validate_sec_configuration
    assert sec.SecConfigurationError is sec_types.SecConfigurationError
    assert issubclass(sec.SecConfigurationError, sec.SecError)
