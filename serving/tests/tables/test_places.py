"""places.parquet (build/tables/places.py)."""
import pytest


def test_places_are_every_hospital_and_every_other_biller_with_a_price(corpus):
    assert corpus.build()["places"] == 6
    assert corpus.rows("places") == [
        ("1000000001", "MIDTOWN IMAGING PC", "practice", "550 PEACHTREE ST NE", "ATLANTA",
         33.7712, -84.3853, None, None),
        ("1000000002", "HIRAM IMAGING LLC", "imaging_center", "4374 ATLANTA HWY", "HIRAM",
         33.8751, -84.7624, None, None),
        # Census didn't match it, so its point is its ZIP's center.
        ("1000000003", "SOUTH GEORGIA RADIOLOGY PC", "practice", "2501 N PATTERSON ST", "VALDOSTA",
         30.8774, -83.2799, None, None),
        # A doctor's own NPI, named after its CMS group.
        ("1000000005", "EMORY MEDICAL CARE FOUNDATION", "practice", "1365 CLIFTON RD NE", "ATLANTA",
         33.792, -84.323, None, None),
        # Every hospital is a place, priced or not; the reader at Grady's address is Grady.
        ("110001", "HAMILTON MEDICAL CENTER", "hospital", "1200 MEMORIAL DRIVE", "DALTON",
         34.7698, -84.9702, "(404) 555-0100", 4),
        ("110079", "GRADY MEMORIAL HOSPITAL", "hospital", "80 JESSE HILL JR DRIVE SE", "ATLANTA",
         33.7524, -84.382, "(404) 555-0100", 3)]


def test_billing_ids_with_a_price_but_no_place_are_listed(corpus, capsys):
    corpus.build()
    out = capsys.readouterr().out
    assert "that have no Georgia NPPES record, so they aren't places (1): 1000000007" in out
    assert "that are a doctor's own NPI with no CMS group, so they aren't places (1): 1000000006" in out


def test_a_place_with_no_point_stops_the_build(corpus):
    corpus.roster(21057, 9, 9, "1000000009", 2000000009)
    corpus.scan_price(21057, "GA Blue Value HIX Individual Network", 9, "72148", all_in=500)
    corpus.billed(2000000009, "72148", "O")
    corpus.npi(1000000009, "PO BOX PRACTICE", "PO BOX 1", "ATLANTA", "399010001")
    with pytest.raises(SystemExit, match=r"places: 1000000009 \(PO BOX PRACTICE\) has no point on the map"):
        corpus.build()
