"""The build-only biller_rates table (integration/inputs/biller_rates.py): who prices
each service, and whether they do it or only read it."""
import pytest

BV = "45334-GAN001"


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
    corpus.scan_price(BV, 1, "72148", all_in=900, service_code="22")  # hospitals only
    corpus.scan_price(BV, 1, "72148", all_in=0.01)
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("reading", 200.0), ("scan", 300.0)]


def test_a_service_priced_only_for_hospitals_keeps_those_lines(corpus):
    corpus.scan_price(BV, 1, "77067", all_in=150, service_code="22")
    corpus.billed(2000000001, "77067", "O")
    assert rates(corpus, "mammogram-screening") == [("all_in", 150.0)]


def test_a_billing_id_that_is_not_an_npi_stops_the_build(corpus):
    corpus.roster(BV, 9, 9, "581234567", 2000000009, tin_type="ein")
    with pytest.raises(SystemExit, match="1 roster rows bill under a 'ein' ID, not an NPI"):
        corpus.build()


def test_a_registered_center_does_its_kind_of_scan_without_a_medicare_record(corpus):
    # 1000000010 is an MRI center Medicare has no record of, priced for an MRI and a
    # mammogram. 1000000011 is a mammography center whose doctor Medicare shows
    # reading MRIs in hospitals.
    for biller, doctor, taxonomy in [(1000000010, 2000000010, "261QM1200X"),
                                     (1000000011, 2000000011, "261QR0206X")]:
        group = biller - 1000000000
        corpus.roster(BV, group, group, str(biller), doctor)
        corpus.scan_price(BV, group, "72148", all_in=500)
        corpus.scan_price(BV, group, "77067", all_in=150)
        corpus.npi(biller, f"CENTER {group}", f"{group} MAIN ST", "ATLANTA", "303082233", taxonomy=taxonomy)
    corpus.billed(2000000011, "72148", "F")
    rows = corpus.inputs().execute("""
        SELECT DISTINCT biller, service_id, does_scan FROM biller_rates
        WHERE biller IN ('1000000010', '1000000011') ORDER BY ALL""").fetchall()
    assert rows == [("1000000010", "mri-lower-back", True),
                    ("1000000011", "mammogram-screening", True),
                    ("1000000011", "mri-lower-back", False)]
