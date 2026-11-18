"""The build-only biller_rates table (integration/inputs/biller_rates.py): who prices
each service, whether they do it or only read it, and the evidence behind each rate."""
import pytest

BV = "45334-GAN001"


def billers(corpus):
    return corpus.inputs().execute(
        "SELECT DISTINCT biller, on_site FROM biller_rates ORDER BY biller").fetchall()


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


def test_a_billing_id_of_an_unknown_type_stops_the_build(corpus):
    corpus.roster(BV, 9, 9, "581234567", 2000000009, tin_type="ssn")
    with pytest.raises(SystemExit, match="1 roster rows bill under a 'ssn' ID, which is neither an NPI nor a tax ID"):
        corpus.build()


def test_a_tax_id_that_is_not_shaped_like_one_stops_the_build(corpus):
    corpus.roster(BV, 9, 9, "581234567", 2000000009, tin_type="ein")
    with pytest.raises(SystemExit, match="the tax ID '581234567', which is not the shape NN-NNNNNNN"):
        corpus.build()


def test_each_npi_in_a_tax_id_group_bills_in_its_own_right_under_its_cms_group(corpus):
    # Two clinicians share a tax ID. Each is its own biller and doctor, and is
    # placed at its NPPES address, under its CMS group's name.
    corpus.roster(BV, 9, 9, "86-2226051", 1000000041, 1000000042, tin_type="ein")
    corpus.scan_price(BV, 9, "72148", all_in=640)
    for npi, group, address, lat in [(1000000041, "PEACHTREE SPINE", "10 SPINE WAY", 33.8),
                                     (1000000042, "MACON BACK CARE", "20 BACK ST", 32.8)]:
        corpus.npi(npi, "DOE", address, "ATLANTA", "303082233", entity_type="individual")
        corpus.group(npi, group)
        corpus.billed(npi, "72148", "O")
        corpus.geocode(address, "ATLANTA", "303082233", f"{address}, ATLANTA, GA, 30308", lat, -84.4)
    con = corpus.inputs()
    assert con.execute("SELECT DISTINCT biller, npi FROM biller_doctors WHERE biller IN ('1000000041', '1000000042') "
                       "ORDER BY ALL").fetchall() == [("1000000041", 1000000041), ("1000000042", 1000000042)]
    assert con.execute("SELECT count(*) FROM biller_rates WHERE biller = '86-2226051'").fetchone() == (0,)
    corpus.build()
    places = {r[0]: r[1] for r in corpus.rows("places")}
    assert places["1000000041"] == "PEACHTREE SPINE"
    assert places["1000000042"] == "MACON BACK CARE"
    assert {r[2] for r in corpus.rows("prices") if r[:2] == (BV, "mri-lower-back")} >= {"1000000041", "1000000042"}


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
        SELECT DISTINCT biller, service_id, on_site FROM biller_rates
        WHERE biller IN ('1000000010', '1000000011') ORDER BY ALL""").fetchall()
    assert rows == [("1000000010", "mri-lower-back", True),
                    ("1000000011", "mammogram-screening", True),
                    ("1000000011", "mri-lower-back", False)]


def test_a_rate_records_the_strongest_evidence_behind_it(corpus):
    # Midtown also sits under a second fee schedule, whose roster has no doctor
    # Medicare shows doing the MRI, and 1000000010 is an MRI center Medicare has
    # no record of.
    corpus.roster(BV, 30, 30, "1000000001", 2000000030)
    corpus.scan_price(BV, 30, "72148", all_in=640)
    corpus.roster(BV, 10, 10, "1000000010", 2000000010)
    corpus.scan_price(BV, 10, "72148", all_in=450)
    corpus.npi(1000000010, "CENTER 10", "10 MAIN ST", "ATLANTA", "303082233", taxonomy="261QM1200X")
    rows = corpus.inputs().execute("""
        SELECT biller, rate, evidence FROM biller_rates
        WHERE biller IN ('1000000001', '1000000010') AND part = 'all_in' ORDER BY ALL""").fetchall()
    assert rows == [("1000000001", 500.0, "medicare_roster"),
                    ("1000000001", 640.0, "medicare_biller"),
                    ("1000000010", 450.0, "registered")]


def test_a_rate_for_another_bill_is_left_out_and_an_unknown_modifier_stops_the_build(corpus):
    corpus.modified_price(BV, 1, "72148", "50", 750)     # both sides in one visit
    corpus.modified_price(BV, 1, "72148", "26|TC", 480)  # two parts at once
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("reading", 200.0), ("scan", 300.0)]
    corpus.modified_price(BV, 1, "72148", "26|ZZ", 90)
    with pytest.raises(SystemExit, match=r"carry modifiers \['ZZ'\], which the build has no rule for"):
        corpus.build()


def test_a_bill_that_names_several_modifiers_takes_a_rate_under_each(corpus):
    scan = next(b for f in corpus.families["families"] if f["family"] == "mri"
                for b in f["bills"] if b["part"] == "scan")
    scan["modifier"] = ["TC", "QW"]
    corpus.write_families()
    corpus.modified_price(BV, 1, "72148", "QW", 310)
    corpus.modified_price(BV, 1, "72148", "QW|TC", 320)  # names two of the scan's
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("reading", 200.0), ("scan", 300.0),
                                               ("scan", 310.0), ("scan", 320.0)]


def test_a_body_site_modifier_prices_the_bill_without_it(corpus):
    corpus.modified_price(BV, 1, "72148", "RT", 520)
    corpus.modified_price(BV, 1, "72148", "26|LT", 210)
    corpus.modified_price(BV, 1, "72148", "F5|TC", 310)
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("all_in", 520.0), ("reading", 200.0),
                                               ("reading", 210.0), ("scan", 300.0), ("scan", 310.0)]


def all_in(corpus, biller="1000000001"):
    return corpus.inputs().execute(
        "SELECT DISTINCT rate FROM biller_rates WHERE biller = ? AND service_id = 'mri-lower-back' "
        "AND part = 'all_in' ORDER BY rate", [biller]).fetchall()


def test_a_biller_with_a_rate_under_the_own_label_ignores_a_higher_one_under_a_shared_label(corpus):
    corpus.roster(BV, 40, 40, "1000000001", 2000000001)
    corpus.scan_price(BV, 40, "72148", all_in=900, own_label=False)
    assert all_in(corpus) == [(500.0,)]


def test_a_biller_with_only_a_shared_label_rate_keeps_it(corpus):
    corpus.roster(BV, 41, 41, "1000000010", 2000000010)
    corpus.npi(1000000010, "CENTER 10", "10 MAIN ST", "ATLANTA", "303082233", taxonomy="261QM1200X")
    corpus.scan_price(BV, 41, "72148", all_in=450, own_label=False)
    assert all_in(corpus, "1000000010") == [(450.0,)]


def test_a_placeholder_under_the_own_label_does_not_hide_a_real_shared_label_rate(corpus):
    corpus.roster(BV, 42, 42, "1000000010", 2000000010)
    corpus.npi(1000000010, "CENTER 10", "10 MAIN ST", "ATLANTA", "303082233", taxonomy="261QM1200X")
    corpus.scan_price(BV, 42, "72148", all_in=0.5)
    corpus.scan_price(BV, 42, "72148", all_in=450, own_label=False)
    assert all_in(corpus, "1000000010") == [(450.0,)]


def test_a_service_keeps_only_the_best_place_of_service_its_family_lists_that_the_network_has(corpus):
    corpus.family("mri", place_of_service=["20", "11"])
    # No line names "20" yet, so the office lines stay.
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("reading", 200.0), ("scan", 300.0)]
    # One line at "20" is the best the network has, so it alone is kept.
    corpus.scan_price(BV, 1, "72148", all_in=999, service_code="20")
    assert rates(corpus, "mri-lower-back") == [("all_in", 999.0)]
    # The family's other order puts the office first again.
    corpus.family("mri", place_of_service=["11", "20"])
    assert rates(corpus, "mri-lower-back") == [("all_in", 500.0), ("reading", 200.0), ("scan", 300.0)]


def test_a_bill_with_its_own_place_of_service_ranks_codes_joined_by_a_bar_equal(corpus):
    corpus.add_endoscopy()
    corpus.service("egd", "43235", family="endoscopy")
    for npi, code, rate in [(1000000031, "22", 400), (1000000032, "24", 450), (1000000033, "21", 999),
                            (1000000034, "11|22", 250), (1000000035, "81", 777)]:
        corpus.npi(npi, f"GI {npi}", "1 MAIN ST", "ATLANTA", "303082233")
        corpus.roster(BV, npi - 1000000000, npi - 1000000000, str(npi), npi)
        corpus.billed(npi, "43235", "F")
        corpus.scan_price(BV, npi - 1000000000, "43235", all_in=rate, service_code=code)
    got = corpus.inputs().execute(
        "SELECT biller, part, rate FROM biller_rates WHERE service_id = 'egd' ORDER BY biller, part").fetchall()
    assert got == [
        ("1000000031", "surgeon", 400.0),      # 22 and 24 are one rank, so both are kept
        ("1000000032", "surgeon", 450.0),
        ("1000000034", "in_office", 250.0),    # a line naming two bills' places counts for both
        ("1000000034", "surgeon", 250.0)]      # 21 ranks below them, and 81 is neither bill's, so both drop


def test_a_bill_with_its_own_place_of_service_ignores_a_family_wide_one(corpus):
    corpus.add_endoscopy()
    corpus.family("endoscopy", place_of_service=["81"])
    corpus.service("egd", "43235", family="endoscopy")
    corpus.npi(1000000031, "GI 31", "1 MAIN ST", "ATLANTA", "303082233")
    corpus.roster(BV, 31, 31, "1000000031", 1000000031)
    corpus.billed(1000000031, "43235", "F")
    corpus.scan_price(BV, 31, "43235", all_in=400, service_code="22")
    corpus.scan_price(BV, 31, "43235", all_in=123, service_code="81")
    assert corpus.inputs().execute("SELECT part, rate FROM biller_rates WHERE service_id = 'egd'").fetchall() == [
        ("surgeon", 400.0)]


def test_a_center_npis_professional_lines_price_no_part_but_a_doctors_do(corpus):
    corpus.add_endoscopy()
    corpus.service("egd", "43235", family="endoscopy")
    for npi, taxonomy in [(1000000051, "261QA1903X"), (1000000052, "261QE0800X"), (1000000053, "2085R0202X")]:
        corpus.npi(npi, f"GI {npi}", "1 MAIN ST", "ATLANTA", "303082233", taxonomy=taxonomy)
        corpus.roster(BV, npi - 1000000000, npi - 1000000000, str(npi), npi)
        corpus.scan_price(BV, npi - 1000000000, "43235", all_in=291, service_code="11")
        corpus.billed(npi, "43235", "O")
    got = corpus.inputs().execute("SELECT DISTINCT biller FROM biller_rates WHERE service_id = 'egd'").fetchall()
    assert got == [("1000000053",)]
