"""The build-only biller_rates table (build/inputs/biller_rates.py): who prices
each service, and whether they do it or only read it."""
import pytest

BV = "GA Blue Value HIX Individual Network"


def billers(corpus):
    return corpus.inputs().execute(
        "SELECT DISTINCT biller, does_scan FROM biller_rates ORDER BY biller").fetchall()


def test_a_biller_does_the_scan_if_medicare_shows_it_in_an_office_or_it_is_an_imaging_center(corpus):
    assert billers(corpus) == [
        ("1000000001", True),   # billed in an office
        ("1000000002", True),   # only in hospitals, but a registered imaging center
        ("1000000003", False),  # only in hospitals: it reads the scan
        ("1000000004", False),
        ("1000000005", True),   # the doctor is the biller, and billed in an office
        ("1000000006", True),
        ("1000000007", True)]   # no place, but still a biller; 1000000008 has no Medicare billing


def rates(corpus, service_id):
    return corpus.inputs().execute(
        "SELECT DISTINCT part, rate FROM biller_rates WHERE biller = '1000000001' AND service_id = ? "
        "ORDER BY part, rate", [service_id]).fetchall()


def test_a_service_with_office_lines_keeps_only_those_and_drops_placeholders(corpus):
    corpus.scan_price(21057, BV, 1, "72148", all_in=900, service_code="22")  # hospitals only
    corpus.scan_price(21057, BV, 1, "72148", all_in=0.01)
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("reading", 200.0), ("scan", 300.0)]


def test_a_service_priced_only_for_hospitals_keeps_those_lines(corpus):
    corpus.scan_price(21057, BV, 1, "77067", all_in=150, service_code="22")
    corpus.billed(2000000001, "77067", "O")
    assert rates(corpus, "mammogram-screening") == [("all_in", 150.0)]


def test_a_billing_id_that_is_not_an_npi_stops_the_build(corpus):
    corpus.roster(21057, 9, 9, "581234567", 2000000009, tin_type="ein")
    with pytest.raises(SystemExit, match="1 roster rows bill under a 'ein' ID, not an NPI"):
        corpus.build()

