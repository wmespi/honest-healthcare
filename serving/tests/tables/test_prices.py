"""prices.parquet (build/tables/prices.py)."""
BV = "GA Blue Value HIX Individual Network"


def prices(corpus):
    corpus.build()
    return {row[2]: row[3:] for row in corpus.rows("prices")}


def part(name, rate):
    return {"part": name, "max": rate}


def biller(corpus, npi, name, address, postal_code, place_of_service, **rates):
    """Price a lower-back MRI at `npi` under its own group set, which Medicare shows
    billing it in an office (O) or a facility (F)."""
    group = npi - 1000000000
    corpus.roster(21057, group, group, str(npi), npi + 1000000000)
    corpus.scan_price(21057, BV, group, "72148", **rates)
    corpus.billed(npi + 1000000000, "72148", place_of_service)
    corpus.npi(npi, name, address, "ATLANTA", postal_code)


def test_each_place_shows_the_bills_a_patient_there_would_get(corpus):
    assert prices(corpus) == {
        # They do the scan, so the all-in rate covers the reading.
        "1000000001": ([part("all_in", 500.0)], False, ["1000000001"]),
        "1000000002": ([part("all_in", 500.0)], False, ["1000000002"]),
        "1000000005": ([part("all_in", 500.0)], False, ["1000000005"]),
        # The group at Grady's address reads it, and the hospital's own fee isn't on file.
        "110079": ([part("facility", None), part("reading", 200.0)], False, ["1000000004"])}
    # South Georgia Radiology isn't on the map, so it has no row.
    assert all(r[:2] == ("45334-GAN001", "mri-lower-back") for r in corpus.rows("prices"))


def test_a_reading_group_elsewhere_shows_an_empty_scan(corpus):
    corpus.geocode("2501 N PATTERSON ST", "VALDOSTA", "316021234", "2501 N PATTERSON ST, VALDOSTA, GA, 31602",
                   30.86, -83.29)
    assert prices(corpus)["1000000003"] == ([part("scan", None), part("reading", 200.0)], False, ["1000000003"])


def test_a_reading_group_with_no_reading_rate_has_no_row(corpus):
    biller(corpus, 1000000023, "SCAN RATE ONLY RADIOLOGY", "550 PEACHTREE ST NE", "303082233", "F", scan=300)
    assert "1000000023" not in prices(corpus)


def test_a_place_that_lists_no_all_in_rate_shows_the_scan_and_the_reading(corpus):
    biller(corpus, 1000000020, "SPLIT BILL IMAGING", "550 PEACHTREE ST NE", "303082233", "O",
           scan=320, reading=180)
    assert prices(corpus)["1000000020"] == ([part("scan", 320.0), part("reading", 180.0)], False, ["1000000020"])


def test_several_rates_for_a_bill_show_the_highest_and_say_so(corpus):
    # A second group reads at Grady for more, and Midtown's second billing ID lists
    # a higher all-in rate.
    biller(corpus, 1000000021, "EMORY RADIOLOGY", "80 JESSE HILL JR DR SE", "303033031", "F", reading=250)
    biller(corpus, 1000000022, "MIDTOWN IMAGING LLC", "550 PEACHTREE ST NE", "303082233", "O", all_in=540)
    rows = prices(corpus)
    assert rows["110079"] == ([part("facility", None), part("reading", 250.0)], True, ["1000000004", "1000000021"])
    assert rows["1000000001"] == ([part("all_in", 540.0)], True, ["1000000001", "1000000022"])
