"""The build-only addresses table (integration/inputs/addresses.py): the building
each address is in, and the building's point on the map."""
MIDTOWN = ("550 PEACHTREE ST NE, ATLANTA, GA, 30308", 33.7712, -84.3853, "census")


def address(corpus, line1):
    return corpus.inputs().execute(
        "SELECT building, lat, lon, located_by FROM addresses WHERE address_line1 = ?", [line1]).fetchall()


def biller(corpus, npi, name, address, city, postal_code):
    """Price a lower-back MRI at `npi`, which Medicare shows doing it in an office."""
    corpus.roster("45334-GAN001", 1, npi - 1000000000, str(npi), npi + 1000000000)
    corpus.billed(npi + 1000000000, "72148", "O")
    corpus.npi(npi, name, address, city, postal_code)


def test_a_suite_census_didnt_locate_is_in_the_building_on_its_street(corpus):
    biller(corpus, 1000000031, "PEACHTREE CARDIOLOGY", "550 PEACHTREE STREET NORTHEAST SUITE 900", "ATLANTA",
           "303082233")
    biller(corpus, 1000000032, "PEACHTREE DERMATOLOGY", "550 Peachtree St. NE # 410", "ATLANTA", "30308")
    assert address(corpus, "550 PEACHTREE STREET NORTHEAST SUITE 900") == [MIDTOWN]
    assert address(corpus, "550 Peachtree St. NE # 410") == [MIDTOWN]


def test_a_street_named_like_a_suite_keeps_its_name(corpus):
    biller(corpus, 1000000033, "SUITE STREET IMAGING", "12 SUITE ST", "MACON", "312011234")
    assert address(corpus, "12 SUITE ST") == [("12 SUITE ST 31201", None, None, None)]


def test_openstreetmap_places_a_building_census_located_no_address_in(corpus):
    # South Georgia Radiology's address, which Census didn't match.
    corpus.geocode("2501 N PATTERSON ST", "VALDOSTA", "316021234", None, None, None, located=False)
    corpus.geocode("2501 N PATTERSON ST", "VALDOSTA", "316021234", None, 30.86, -83.29, source="openstreetmap")
    assert address(corpus, "2501 N PATTERSON ST") == [("2501 N PATTERSON ST 31602", 30.86, -83.29, "openstreetmap")]
    corpus.build()
    assert ("1000000003", "SOUTH GEORGIA RADIOLOGY PC", "practice", "Medical practice", "2501 N PATTERSON ST", "VALDOSTA",
            30.86, -83.29, None, None) in corpus.rows("places")
    assert corpus.rows("unlocated_places") == []


def test_a_buildings_point_is_census_before_openstreetmap_and_the_hospitals_own_first(corpus):
    corpus.geocode("550 PEACHTREE ST NE", "ATLANTA", "303082233", None, 33.9, -84.5, source="openstreetmap")
    assert address(corpus, "550 PEACHTREE ST NE") == [MIDTOWN]
    # Census standardized a group on Hamilton's street to another ZIP, and it's still in Hamilton's building.
    biller(corpus, 1000000034, "DALTON RADIOLOGY", "1200 MEMORIAL DR", "DALTON", "307202529")
    corpus.geocode("1200 MEMORIAL DR", "DALTON", "307202529", "1200 MEMORIAL DR, DALTON, GA, 30721", 34.75, -84.95)
    assert address(corpus, "1200 MEMORIAL DR") == [("1200 MEMORIAL DR, DALTON, GA, 30720", 34.7698, -84.9702, "census")]
