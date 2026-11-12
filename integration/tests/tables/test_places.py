"""places.parquet and unlocated_places.parquet (integration/tables/places.py)."""
import hashlib


def test_places_are_every_hospital_and_every_practice_with_a_price_on_the_map(corpus):
    assert corpus.build()["places"] == 5
    # South Georgia Radiology, which Census didn't match, isn't one.
    assert corpus.rows("places") == [
        ("1000000001", "MIDTOWN IMAGING PC", "practice", "550 PEACHTREE ST NE", "ATLANTA",
         33.7712, -84.3853, None, None),
        ("1000000002", "HIRAM IMAGING LLC", "imaging_center", "4374 ATLANTA HWY", "HIRAM",
         33.8751, -84.7624, None, None),
        # A doctor's own NPI, named after its CMS group.
        ("1000000005", "EMORY MEDICAL CARE FOUNDATION", "practice", "1365 CLIFTON RD NE", "ATLANTA",
         33.792, -84.323, None, None),
        # Every hospital is a place, priced or not; the reader at Grady's address is Grady.
        ("110001", "HAMILTON MEDICAL CENTER", "hospital", "1200 MEMORIAL DRIVE", "DALTON",
         34.7698, -84.9702, "(404) 555-0100", 4),
        ("110079", "GRADY MEMORIAL HOSPITAL", "hospital", "80 JESSE HILL JR DRIVE SE", "ATLANTA",
         33.7524, -84.382, "(404) 555-0100", 3)]


def biller(corpus, npi, name, address, city, postal_code, **fields):
    """Price a lower-back MRI at `npi`, which Medicare shows doing it in an office."""
    corpus.roster("45334-GAN001", 1, npi - 1000000000, str(npi), npi + 1000000000)
    corpus.billed(npi + 1000000000, "72148", "O")
    corpus.npi(npi, name, address, city, postal_code, **fields)


def test_billing_ids_of_one_practice_at_one_address_are_one_place(corpus):
    # Midtown Imaging's second billing ID, spelled differently, in a suite Census
    # didn't match and registered as an imaging center, and an orthopedic group in
    # the same building.
    biller(corpus, 1000000011, "MIDTOWN IMAGING, P.C.", "550 PEACHTREE STREET NORTHEAST", "ATLANTA",
           "303082233", taxonomy="261QR0200X", address_line2="STE 200")
    biller(corpus, 1000000012, "PEACHTREE ORTHOPEDICS LLC", "550 PEACHTREE ST NE", "ATLANTA", "303082233",
           taxonomy="207X00000X", address_line2="STE 300")
    corpus.geocode("550 PEACHTREE ST NE", "ATLANTA", "303082233", "550 PEACHTREE ST NE, ATLANTA, GA, 30308",
                   33.7712, -84.3853, address_line2="STE 300")
    corpus.build()
    midtown = [r for r in corpus.rows("places") if r[3].startswith("550 PEACHTREE")]
    # The practice keeps its lowest NPI's name and address, and is an imaging
    # center because one of its billers is registered as one.
    assert midtown == [
        ("1000000001", "MIDTOWN IMAGING PC", "imaging_center", "550 PEACHTREE ST NE", "ATLANTA",
         33.7712, -84.3853, None, None),
        ("1000000012", "PEACHTREE ORTHOPEDICS LLC", "practice", "550 PEACHTREE ST NE, STE 300", "ATLANTA",
         33.7712, -84.3853, None, None)]
    assert corpus.inputs().execute("""
        SELECT biller, place_id FROM biller_places WHERE biller IN ('1000000011', '1000000012')
        ORDER BY biller""").fetchall() == [("1000000011", "1000000001"), ("1000000012", "1000000012")]


def test_a_billing_id_on_a_hospitals_street_is_the_hospital(corpus, capsys):
    # Census didn't match Hamilton's own billing ID, whose address spells the street
    # differently from the hospital's.
    biller(corpus, 1000000013, "HAMILTON MEDICAL CENTER, INC.", "1200 MEMORIAL DR", "DALTON", "307202529",
           taxonomy="282N00000X")
    # The hospital's address names its cross street.
    corpus.hospital("110038", "ARCHBOLD MEMORIAL HOSPITAL", "915 GORDON AVENUE & MIMOSA DRIVE", "THOMASVILLE",
                    "31792")
    biller(corpus, 1000000014, "JOHN D ARCHBOLD MEMORIAL HOSPITAL INC", "915 GORDON AVE", "THOMASVILLE",
           "317926614", taxonomy="282N00000X")
    # A hospital's own billing ID at an address no hospital has.
    biller(corpus, 1000000015, "PERRY HOSPITAL INC", "1120 MORNINGSIDE DR", "PERRY", "310692906",
           taxonomy="282N00000X")
    assert corpus.inputs().execute("""
        SELECT biller, place_id, kind FROM biller_places WHERE biller > '1000000012' ORDER BY biller
    """).fetchall() == [("1000000013", "110001", "hospital"), ("1000000014", "110038", "hospital"),
                        ("1000000015", "1000000015", "practice")]
    assert ("billing IDs NPPES registers as hospitals that match no hospital's address, so they're "
            "practices (1): 1000000015 (PERRY HOSPITAL INC)") in capsys.readouterr().out


def test_billing_ids_with_a_price_but_no_place_are_listed(corpus, capsys):
    corpus.build()
    out = capsys.readouterr().out
    assert "that have no Georgia NPPES record, so they aren't places (1): 1000000007" in out
    assert "that are a doctor's own NPI with no CMS group, so they aren't places (1): 1000000006" in out


def test_a_place_census_cant_put_on_the_map_is_left_off_and_saved(corpus):
    # A suite Census didn't match takes the point of its building, which it did.
    biller(corpus, 1000000016, "PEACHTREE CARDIOLOGY", "550 PEACHTREE ST NE", "ATLANTA", "303082233",
           taxonomy="207RC0000X", address_line2="STE 900")
    corpus.hospital("110038", "ARCHBOLD MEMORIAL HOSPITAL", "915 GORDON AVENUE & MIMOSA DRIVE", "THOMASVILLE",
                    "31792")
    assert corpus.build()["unlocated_places"] == 2
    assert ("1000000016", "PEACHTREE CARDIOLOGY", "practice", "550 PEACHTREE ST NE, STE 900", "ATLANTA",
            33.7712, -84.3853, None, None) in corpus.rows("places")
    # No address in South Georgia Radiology's building or Archbold's matched.
    assert corpus.rows("unlocated_places") == [
        ("1000000003", "SOUTH GEORGIA RADIOLOGY PC", "practice", "2501 N PATTERSON ST", "VALDOSTA", "31602"),
        ("110038", "ARCHBOLD MEMORIAL HOSPITAL", "hospital", "915 GORDON AVENUE & MIMOSA DRIVE", "THOMASVILLE",
         "31792")]


def test_a_census_match_outside_georgia_counts_as_no_match(corpus):
    # Census put this LaFayette address in LaFayette, Tennessee.
    biller(corpus, 1000000017, "LAFAYETTE IMAGING", "100 MAIN ST", "LAFAYETTE", "307281234")
    corpus.geocode("100 MAIN ST", "LAFAYETTE", "307281234", "100 MAIN ST, LAFAYETTE, TN, 37083", 36.52, -86.03,
                   located=False)
    corpus.build()
    assert ("1000000017", "LAFAYETTE IMAGING", "practice", "100 MAIN ST", "LAFAYETTE", "30728") in corpus.rows(
        "unlocated_places")


def test_a_billers_other_address_is_another_place_with_its_own_phone(corpus):
    # A registered imaging center, shown at every place, lists a second office in NPPES.
    biller(corpus, 1000000018, "DECATUR IMAGING LLC", "100 CLAIREMONT AVE", "DECATUR", "300302222",
           taxonomy="261QR0200X", phone="4045550118")
    corpus.location(1000000018, "200 N MAIN ST", "ALPHARETTA", "300091234", phone="770555")
    corpus.geocode("100 CLAIREMONT AVE", "DECATUR", "300302222", "100 CLAIREMONT AVE, DECATUR, GA, 30030",
                   33.7748, -84.2963)
    corpus.geocode("200 N MAIN ST", "ALPHARETTA", "300091234", "200 N MAIN ST, ALPHARETTA, GA, 30009",
                   34.0778, -84.2954)
    corpus.build()
    # No biller's own record is in the second building, so its ID adds the building's hash.
    other = "1000000018-" + hashlib.md5(b"200 N MAIN ST, ALPHARETTA, GA, 30009").hexdigest()[:8]
    assert [r for r in corpus.rows("places") if r[1] == "DECATUR IMAGING LLC"] == [
        ("1000000018", "DECATUR IMAGING LLC", "imaging_center", "100 CLAIREMONT AVE", "DECATUR",
         33.7748, -84.2963, "(404) 555-0118", None),
        # A phone that isn't ten digits is left out.
        (other, "DECATUR IMAGING LLC", "imaging_center", "200 N MAIN ST", "ALPHARETTA",
         34.0778, -84.2954, None, None)]


def test_a_billers_other_address_in_its_practices_building_or_a_hospitals_joins_it(corpus):
    # Midtown Imaging's second billing ID has a billing office on its record, and
    # lists Midtown's building and Grady's as its other addresses.
    biller(corpus, 1000000019, "MIDTOWN IMAGING, P.C.", "9 BILLING OFFICE WAY", "ATLANTA", "303091234")
    corpus.location(1000000019, "550 PEACHTREE STREET NE", "ATLANTA", "30308", address_line2="STE 200")
    corpus.location(1000000019, "80 JESSE HILL JR DR SE", "ATLANTA", "303033031")
    assert corpus.inputs().execute("""
        SELECT place_id, kind, main FROM biller_places WHERE biller = '1000000019' ORDER BY place_id
    """).fetchall() == [("1000000001", "practice", False), ("1000000019", "practice", True),
                        ("110079", "hospital", False)]
    # With no doctor of its own seen anywhere, only its main place is one a service is shown at.
    corpus.build()
    assert ("1000000019", "MIDTOWN IMAGING, P.C.", "practice", "9 BILLING OFFICE WAY", "ATLANTA",
            "30309") in corpus.rows("unlocated_places")
