"""prices.parquet (integration/tables/prices.py)."""
BV, PATHWAY = "45334-GAN001", "49046-GAN008"


def prices(corpus):
    """Each place's parts, several_rates and billers."""
    corpus.build()
    return {row[2]: row[3:6] for row in corpus.rows("prices")}


def providers(corpus):
    """Each row's providers, by network, service and place."""
    corpus.build()
    return {row[:3]: row[6] for row in corpus.rows("prices")}


def person(corpus, npi, last_name):
    corpus.npi(npi, last_name, "1 DOCTOR WAY", "ATLANTA", "303082233", entity_type="individual")


def part(name, rate):
    return {"part": name, "max": rate}


def biller(corpus, npi, name, address, postal_code, place_of_service, **rates):
    """Price a lower-back MRI at `npi` under its own group set, which Medicare shows
    billing it in an office (O) or a facility (F)."""
    group = npi - 1000000000
    corpus.roster(BV, group, group, str(npi), npi + 1000000000)
    corpus.scan_price(BV, group, "72148", **rates)
    corpus.billed(npi + 1000000000, "72148", place_of_service)
    corpus.npi(npi, name, address, "ATLANTA", postal_code)


def test_each_place_shows_the_bills_a_patient_there_would_get(corpus):
    assert prices(corpus) == {
        # They do the scan, so the all-in rate covers the reading.
        "1000000001": ([part("all_in", 500.0)], False, ["MIDTOWN IMAGING PC"]),
        "1000000002": ([part("all_in", 500.0)], False, ["HIRAM IMAGING LLC"]),
        "1000000005": ([part("all_in", 500.0)], False, ["EMORY MEDICAL CARE FOUNDATION"]),
        # The group at Grady's address reads it, and the hospital's own fee isn't on file.
        "110079": ([part("facility", None), part("reading", 200.0)], False, ["GRADY RADIOLOGY GROUP"])}
    # South Georgia Radiology isn't on the map, so it has no row.
    assert all(r[:2] == ("45334-GAN001", "mri-lower-back") for r in corpus.rows("prices"))


def test_a_reading_group_elsewhere_shows_an_empty_scan(corpus):
    corpus.geocode("2501 N PATTERSON ST", "VALDOSTA", "316021234", "2501 N PATTERSON ST, VALDOSTA, GA, 31602",
                   30.86, -83.29)
    assert prices(corpus)["1000000003"] == ([part("scan", None), part("reading", 200.0)], False,
                                            ["SOUTH GEORGIA RADIOLOGY PC"])


def test_a_reading_group_with_no_reading_rate_has_no_row(corpus):
    biller(corpus, 1000000023, "SCAN RATE ONLY RADIOLOGY", "550 PEACHTREE ST NE", "303082233", "F", scan=300)
    assert "1000000023" not in prices(corpus)


def test_a_place_that_lists_no_all_in_rate_shows_the_scan_and_the_reading(corpus):
    biller(corpus, 1000000020, "SPLIT BILL IMAGING", "550 PEACHTREE ST NE", "303082233", "O",
           scan=320, reading=180)
    assert prices(corpus)["1000000020"] == ([part("scan", 320.0), part("reading", 180.0)], False,
                                            ["SPLIT BILL IMAGING"])


def test_a_rate_with_stronger_evidence_is_shown_over_a_higher_one(corpus):
    # Midtown also sits under a second fee schedule, for more, whose roster has no
    # doctor Medicare shows doing the MRI.
    corpus.roster(BV, 30, 30, "1000000001", 2000000030)
    corpus.scan_price(BV, 30, "72148", all_in=640)
    assert prices(corpus)["1000000001"] == ([part("all_in", 500.0)], False, ["MIDTOWN IMAGING PC"])


def test_several_rates_with_the_same_evidence_show_the_highest_and_say_so(corpus):
    # A second group reads at Grady for more, and Midtown's second billing ID lists
    # a higher all-in rate.
    biller(corpus, 1000000021, "EMORY RADIOLOGY", "80 JESSE HILL JR DR SE", "303033031", "F", reading=250)
    biller(corpus, 1000000022, "MIDTOWN IMAGING LLC", "550 PEACHTREE ST NE", "303082233", "O", all_in=540)
    rows = prices(corpus)
    assert rows["110079"] == ([part("facility", None), part("reading", 250.0)], True,
                              ["EMORY RADIOLOGY", "GRADY RADIOLOGY GROUP"])
    # Midtown's billing IDs spell its name two ways, and it keeps its lowest NPI's.
    assert rows["1000000001"] == ([part("all_in", 540.0)], True, ["MIDTOWN IMAGING PC"])


def test_a_row_lists_the_clinicians_medicare_shows_billing_the_service_there(corpus):
    for npi, last_name in [(2000000001, "OKAFOR"), (2000000002, "LINDQVIST"), (2000000003, "HADDAD"),
                           (2000000004, "PRESCOTT"), (2000000008, "IVERSON")]:
        person(corpus, npi, last_name)
    corpus.billed(1000000001, "72148", "O")  # an organization's own NPI isn't a clinician
    mri = "45334-GAN001", "mri-lower-back"
    assert providers(corpus) == {
        (*mri, "1000000001"): [2000000001],
        (*mri, "1000000002"): [2000000002],
        (*mri, "1000000005"): [1000000005],  # the doctor bills under their own NPI
        (*mri, "110079"): [2000000004]}
    # 2000000003's group isn't on the map, and Medicare shows 2000000008 billing nothing.


def test_a_row_lists_only_the_clinicians_who_bill_its_service_on_its_network(corpus):
    # A second doctor at Midtown bills only mammograms. On Pathway, the doctor
    # who does Midtown's MRIs on Blue Value reads at Grady instead.
    person(corpus, 2000000001, "OKAFOR")
    person(corpus, 2000000031, "NAKAMURA")
    corpus.roster(BV, 1, 1, "1000000001", 2000000031)
    corpus.scan_price(BV, 1, "77067", all_in=150)
    corpus.billed(2000000031, "77067", "O")
    corpus.roster(PATHWAY, 4, 4, "1000000004", 2000000001)
    corpus.scan_price(PATHWAY, 4, "72148", reading=210)
    rows = providers(corpus)
    assert rows[("45334-GAN001", "mri-lower-back", "1000000001")] == [2000000001]
    assert rows[("45334-GAN001", "mammogram-screening", "1000000001")] == [2000000031]
    assert rows[("45334-GAN001", "mri-lower-back", "110079")] == []
    assert rows[("49046-GAN008", "mri-lower-back", "110079")] == [2000000001]


def test_a_doctor_billing_at_a_hospital_under_their_own_npi_is_named_for_their_group_or_themselves(corpus):
    for npi, last_name in [(1000000024, "KIM"), (1000000025, "LEE")]:
        group = npi - 1000000000
        corpus.roster(BV, group, group, str(npi), npi)
        corpus.scan_price(BV, group, "72148", reading=210)
        corpus.billed(npi, "72148", "F")
        corpus.npi(npi, last_name, "80 JESSE HILL JR DR SE", "ATLANTA", "303033031", entity_type="individual")
    corpus.group(1000000024, "ATLANTA RADIOLOGY ASSOCIATES")
    assert prices(corpus)["110079"][2] == ["ATLANTA RADIOLOGY ASSOCIATES", "GRADY RADIOLOGY GROUP", "PAT LEE"]


def test_a_place_names_the_clinicians_cms_lists_practicing_there(corpus):
    # Midtown Imaging lists a second office, and CMS lists one of its two MRI doctors there.
    person(corpus, 2000000001, "OKAFOR")
    person(corpus, 2000000032, "ADEYEMI")
    corpus.roster(BV, 1, 1, "1000000001", 2000000032)
    corpus.billed(2000000032, "72148", "O")
    corpus.location(1000000001, "200 N MAIN ST", "ALPHARETTA", "300091234")
    corpus.geocode("200 N MAIN ST", "ALPHARETTA", "300091234", "200 N MAIN ST, ALPHARETTA, GA, 30009",
                   34.0778, -84.2954)
    corpus.practices_at(2000000032, "200 N MAIN ST", "ALPHARETTA", "300091234", address_line2="STE 5")
    rows = providers(corpus)
    office = [place for network, service, place in rows if place.startswith("1000000001-")]
    assert len(office) == 1 and rows[(BV, "mri-lower-back", office[0])] == [2000000032]
    # Its doctors are seen at the second office, so the MRI isn't shown at the first.
    assert (BV, "mri-lower-back", "1000000001") not in rows
    corpus.practices_at(2000000001, "550 PEACHTREE ST NE", "ATLANTA", "303082233")
    rows = providers(corpus)
    assert rows[(BV, "mri-lower-back", "1000000001")] == [2000000001]
    assert rows[(BV, "mri-lower-back", office[0])] == [2000000032]
