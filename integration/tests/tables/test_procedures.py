"""A procedure's bills in prices.parquet (integration/tables/prices.py): the
facility's own fee at a hospital or a surgery center, and the doctor's fee in an
office or, apart, at a facility (integration/families.yaml)."""
BV = "45334-GAN001"
EGD = "egd"
HOSPITAL_GROUP, OFFICE, FACILITY_ONLY = 1000000022, 1000000023, 1000000024
HOSPITAL_NPI = 1000000025
LONE_ASC, ENDO_CENTER, ASC, ASC_GROUP = 1000000026, 1000000027, 1000000028, 1000000029
GRADY = "80 JESSE HILL JR DR SE"


def part(name, rate, source="plan"):
    return {"part": name, "max": rate, "terms": None, "source": None if rate is None else source}


def place(corpus, npi, name, address, postal_code, taxonomy="2085R0202X", geocoded=False):
    """A billing organization at an address, which the corpus has geocoded already
    or this does."""
    corpus.npi(npi, name, address, "ATLANTA", postal_code, taxonomy=taxonomy)
    if not geocoded:
        corpus.geocode(address, "ATLANTA", postal_code, f"{address}, ATLANTA, GA, {postal_code[:5]}", 33.75, -84.38)
    corpus.roster(BV, npi - 1000000000, npi - 1000000000, str(npi), npi + 1000000000)


def procedures(corpus):
    """The corpus with the procedure family and one service in it, priced by four
    places on Blue Value, then built: a surgery center with only a facility fee, a
    group billing at Grady, an office Medicare shows doing it, and an office that
    only does it in facilities."""
    corpus.add_endoscopy()
    corpus.service(EGD, "43235", family="endoscopy", name="Upper endoscopy")
    # A generic surgery center with a fee and no group with evidence in its building.
    place(corpus, LONE_ASC, "UROLOGY SURGERY CENTER", "10 SURGERY WAY", "303082233", taxonomy="261QA1903X")
    corpus.facility_price(BV, LONE_ASC - 1000000000, "43235", 900)
    # An endoscopy center, with a fee and no doctor placed there.
    place(corpus, ENDO_CENTER, "PEACHTREE ENDOSCOPY CENTER", "50 ENDO WAY", "303082233", taxonomy="261QE0800X")
    corpus.facility_price(BV, ENDO_CENTER - 1000000000, "43235", 700)
    # A generic surgery center whose building a group with evidence works in.
    place(corpus, ASC, "MIDTOWN SURGERY CENTER", "40 ASC WAY", "303082233", taxonomy="261QA1903X")
    corpus.facility_price(BV, ASC - 1000000000, "43235", 950)
    place(corpus, ASC_GROUP, "MIDTOWN GI GROUP", "40 ASC WAY", "303082233", geocoded=True)
    corpus.scan_price(BV, ASC_GROUP - 1000000000, "43235", all_in=420, service_code="22")
    corpus.billed(ASC_GROUP + 1000000000, "43235", "F")
    corpus.scan_price(BV, ASC - 1000000000, "43235", all_in=291, service_code="11")  # not a doctor's fee
    place(corpus, HOSPITAL_GROUP, "GRADY GI GROUP", GRADY, "303033031", geocoded=True)
    corpus.scan_price(BV, HOSPITAL_GROUP - 1000000000, "43235", all_in=400, service_code="22")
    corpus.billed(HOSPITAL_GROUP + 1000000000, "43235", "F")
    corpus.npi(HOSPITAL_NPI, "GRADY MEMORIAL HOSPITAL", GRADY, "ATLANTA", "303033031", taxonomy="282N00000X")
    corpus.roster(BV, 25, 25, str(HOSPITAL_NPI), HOSPITAL_NPI)
    corpus.facility_price(BV, 25, "43235", 1200)
    place(corpus, OFFICE, "PIEDMONT DIGESTIVE PC", "20 OFFICE WAY", "303082233")
    corpus.scan_price(BV, OFFICE - 1000000000, "43235", all_in=300, service_code="11")
    corpus.billed(OFFICE + 1000000000, "43235", "O")
    place(corpus, FACILITY_ONLY, "MIDTOWN GI PC", "30 CLINIC WAY", "303082233")
    corpus.scan_price(BV, FACILITY_ONLY - 1000000000, "43235", all_in=500, service_code="22")
    corpus.billed(FACILITY_ONLY + 1000000000, "43235", "F")
    corpus.build()
    return {r[2]: r[3] for r in corpus.rows("prices") if r[:2] == (BV, EGD)}


def places(corpus):
    return {r[0]: (r[1], r[2], r[3]) for r in corpus.rows("places")}


def test_a_generic_surgery_center_with_a_fee_and_no_group_with_evidence_has_no_row_and_is_no_place(corpus):
    shown = procedures(corpus)
    assert str(LONE_ASC) not in shown and str(LONE_ASC) not in places(corpus)


def test_a_surgery_center_with_a_group_with_evidence_in_its_building_shows_its_fee_and_theirs(corpus):
    shown = procedures(corpus)
    assert shown[str(ASC_GROUP)] == [part("facility", 950.0), part("surgeon", 420.0)]
    assert places(corpus)[str(ASC_GROUP)][1:] == ("practice", "Medical practice")
    assert str(ASC) not in places(corpus)


def test_an_endoscopy_center_with_a_fee_and_no_doctor_shows_the_facility_fee(corpus):
    assert procedures(corpus)[str(ENDO_CENTER)] == [part("facility", 700.0), part("surgeon", None)]
    assert places(corpus)[str(ENDO_CENTER)][1] == "surgery_center"


def test_a_hospital_shows_its_facility_fee_and_the_doctors_fee_of_a_group_in_its_building(corpus):
    assert procedures(corpus)["110079"] == [part("facility", 1200.0), part("surgeon", 400.0)]


def test_a_practice_medicare_shows_doing_it_in_the_office_shows_that_bill(corpus):
    shown = procedures(corpus)
    assert shown[str(OFFICE)] == [part("in_office", 300.0)]
    assert places(corpus)[str(OFFICE)][1] == "practice"


def test_a_practice_that_does_it_only_in_facilities_shows_the_doctors_fee_and_an_empty_facility_fee(corpus):
    assert procedures(corpus)[str(FACILITY_ONLY)] == [part("facility", None), part("surgeon", 500.0)]


def test_a_biller_with_no_place_of_its_own_gets_one_only_as_a_center_billing_a_facility_fee(corpus):
    procedures(corpus)
    assert str(HOSPITAL_NPI) not in {r[0] for r in corpus.rows("places")}
    assert {r[0] for r in corpus.rows("places") if r[2] == "surgery_center"} == {str(ENDO_CENTER)}


def test_a_group_and_a_lower_generic_surgery_center_npi_at_one_address_are_one_practice_keyed_on_the_group(corpus):
    procedures(corpus)
    low, group = 1000000009, 1000000030
    place(corpus, group, "THE EMORY CLINIC INC", "60 CLINIC WAY", "303082233")
    corpus.scan_price(BV, group - 1000000000, "43235", all_in=330, service_code="22")
    corpus.billed(group + 1000000000, "43235", "F")
    place(corpus, low, "THE EMORY CLINIC INC", "60 CLINIC WAY", "303082233", taxonomy="261QA1903X", geocoded=True)
    corpus.facility_price(BV, low - 1000000000, "43235", 640)
    corpus.build()
    assert str(low) not in places(corpus)
    assert places(corpus)[str(group)][1:] == ("practice", "Medical practice")
    assert {r[2]: r[3] for r in corpus.rows("prices") if r[:2] == (BV, EGD)}[str(group)] == [
        part("facility", 640.0), part("surgeon", 330.0)]


def test_an_endoscopy_center_merged_with_a_group_stays_a_surgery_center(corpus):
    procedures(corpus)
    group, center = 1000000031, 1000000032
    place(corpus, group, "RIVER GI", "70 RIVER WAY", "303082233")
    corpus.scan_price(BV, group - 1000000000, "43235", all_in=330, service_code="11")
    corpus.billed(group + 1000000000, "43235", "O")
    place(corpus, center, "RIVER GI", "70 RIVER WAY", "303082233", taxonomy="261QE0800X", geocoded=True)
    corpus.facility_price(BV, center - 1000000000, "43235", 640)
    corpus.build()
    assert places(corpus)[str(group)][1] == "surgery_center"
