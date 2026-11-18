"""The build-only service_sites table (integration/inputs/service_sites.py): the
places where each biller's rate for a service is shown."""
import hashlib

BV = "45334-GAN001"
GROUP, CLINIC = "1000000041", "1000000041-" + hashlib.md5(b"200 N MAIN ST 30009").hexdigest()[:8]


def sites(corpus):
    return corpus.inputs().execute(
        "SELECT place_id, providers FROM service_sites WHERE biller = ? ORDER BY place_id", [GROUP]).fetchall()


def group(corpus, taxonomy="2085R0202X"):
    """Price a lower-back MRI at a group with a billing office on its NPPES record
    and a clinic it lists as another address, with two doctors Medicare shows
    doing the MRI in an office."""
    corpus.roster(BV, 1, 41, GROUP, 2000000041, 2000000042)
    for doctor in (2000000041, 2000000042):
        corpus.billed(doctor, "72148", "O")
    corpus.npi(int(GROUP), "NORTHSIDE RADIOLOGY", "9 BILLING OFFICE WAY", "ATLANTA", "303091234", taxonomy=taxonomy)
    corpus.location(int(GROUP), "200 N MAIN ST", "ALPHARETTA", "300091234")


def test_a_service_is_shown_where_cms_lists_its_doctors_practicing(corpus):
    group(corpus)
    corpus.practices_at(2000000041, "200 NORTH MAIN STREET", "ALPHARETTA", "30009", address_line2="STE 5")
    # The other doctor practices where the group has no place.
    corpus.practices_at(2000000042, "77 ELSEWHERE RD", "ROSWELL", "30075")
    assert sites(corpus) == [(CLINIC, [2000000041])]


def test_a_biller_with_no_doctor_seen_at_its_places_is_shown_at_its_main_one(corpus):
    group(corpus)
    corpus.practices_at(2000000042, "77 ELSEWHERE RD", "ROSWELL", "30075")
    assert sites(corpus) == [(GROUP, [2000000041, 2000000042])]


def test_a_registered_center_is_shown_at_every_place(corpus):
    group(corpus, taxonomy="261QR0200X")
    assert sites(corpus) == [(GROUP, [2000000041, 2000000042]), (CLINIC, [])]
    corpus.practices_at(2000000041, "200 N MAIN ST", "ALPHARETTA", "300091234")
    assert sites(corpus) == [(GROUP, []), (CLINIC, [2000000041])]


def test_a_service_is_shown_at_a_hospital_cms_affiliates_one_of_the_billers_doctors_with(corpus):
    corpus.family("mri", affiliated_doctors=True)
    group(corpus)
    corpus.works_at(2000000041, "110079")
    assert sites(corpus) == [(GROUP, [2000000041, 2000000042]), ("110079", [2000000041])]
    # The affiliation isn't a sighting at the biller's own places: its main place keeps every doctor, and a
    # place where CMS does list a doctor still hides the main one.
    corpus.practices_at(2000000041, "200 NORTH MAIN STREET", "ALPHARETTA", "30009", address_line2="STE 5")
    assert sites(corpus) == [(CLINIC, [2000000041]), ("110079", [2000000041])]


def test_a_hospital_that_isnt_in_the_hospitals_list_adds_nothing(corpus):
    corpus.family("mri", affiliated_doctors=True)
    group(corpus)
    corpus.works_at(2000000041, "110999")
    assert sites(corpus) == [(GROUP, [2000000041, 2000000042])]


def test_a_family_that_doesnt_take_affiliated_doctors_gets_no_affiliation_row(corpus):
    # A scan's reader is whoever reads at the hospital, so the imaging families don't take them.
    group(corpus)
    corpus.works_at(2000000041, "110079")
    assert sites(corpus) == [(GROUP, [2000000041, 2000000042])]
