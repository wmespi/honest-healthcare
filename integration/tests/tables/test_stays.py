"""A hospital stay's bill in prices.parquet and the doctor's part of extra_bills.parquet
(integration/families.yaml `stays`, integration/inputs/facility_fees.py)."""
import pytest

from integration.tests.corpus import BLUE_VALUE as BV, PATHWAY

GRADY, HAMILTON = "110079", "110001"
VAGINAL, C_SECTION, KNEE, HIP = "stay-vaginal-delivery", "stay-c-section", "stay-knee-replacement", "stay-hip-replacement"
NAMES = {BV: ["ANTHEM BCBS | BLUE VALUE HMO"], PATHWAY: ["ANTHEM BCBS | PATHWAY"]}


def stays(corpus):
    """The corpus with the stays family and its four services, Grady and Hamilton each
    on both networks' names in the reviewed list."""
    corpus.add_stays()
    corpus.hospital_plans([{"hospitals": [GRADY, HAMILTON], "networks": NAMES, "other": ["ANTHEM BCBS | MEDICARE ADVANTAGE"]}])


def drg(corpus, group_set, code, rate, **fields):
    corpus.facility_price(PATHWAY, group_set, code, rate, setting=fields.pop("setting", "inpatient"),
                          code_type="MS-DRG", negotiated_type=fields.pop("negotiated_type", "negotiated"), **fields)


def stay_prices(corpus, network_id, service_id):
    """Each place's stay part for a service on a network."""
    corpus.build()
    return {r[2]: r[3] for r in corpus.rows("prices") if r[:2] == (network_id, service_id)}


def parts(corpus, network_id, service_id):
    return {place: [{k: p[k] for k in ("max", "terms", "source")} for p in ps]
            for place, ps in stay_prices(corpus, network_id, service_id).items()}


def test_the_plans_negotiated_dollars_beat_the_hospitals_own_file(corpus):
    stays(corpus)
    drg(corpus, 50, "807", 5000)
    drg(corpus, 50, "807", 6500)
    drg(corpus, 50, "807", 2400, negotiated_type="per diem")  # a day's rate loses to a dollar amount
    corpus.hospital_price(GRADY, "807", "ANTHEM BCBS", "PATHWAY", negotiated=9000.0, setting="inpatient",
                          code_type="MS-DRG")
    corpus.hospital_price(GRADY, "807", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=6000.0, setting="inpatient",
                          code_type="MS-DRG", methodology="case rate")
    corpus.hospital_price(GRADY, "807", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=4000.0, setting="inpatient",
                          code_type="MS-DRG", methodology="per diem")
    assert parts(corpus, PATHWAY, VAGINAL) == {GRADY: [{"max": 6500.0, "terms": None, "source": "plan"}]}
    assert parts(corpus, BV, VAGINAL) == {GRADY: [{"max": 6000.0, "terms": None, "source": "hospital"}]}
    row = [r for r in corpus.rows("prices") if r[:3] == (PATHWAY, VAGINAL, GRADY)][0]
    assert row[4]  # two different dollar amounts: several rates


def test_a_per_day_or_percent_price_is_terms_with_no_max_and_the_hospital_still_has_a_row(corpus):
    stays(corpus)
    drg(corpus, 50, "807", 2472, negotiated_type="per diem")  # Grady's plan file: a day's rate
    corpus.hospital_price(HAMILTON, "807", "ANTHEM BCBS", "BLUE VALUE HMO", setting="inpatient", code_type="MS-DRG",
                          methodology="percent of total billed charges", percentage=38.8)
    corpus.hospital_price(HAMILTON, "788", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=1800.0, setting="inpatient",
                          code_type="MS-DRG", methodology="per diem")
    corpus.hospital_price(HAMILTON, "788", "ANTHEM BCBS", "BLUE VALUE HMO", setting="inpatient", code_type="MS-DRG",
                          methodology="percent of total billed charges", percentage=50)  # the per diem comes first
    assert parts(corpus, PATHWAY, VAGINAL) == {GRADY: [{"max": None, "terms": "$2,472 a day", "source": "plan"}]}
    assert parts(corpus, BV, VAGINAL) == {
        HAMILTON: [{"max": None, "terms": "39% of the hospital’s charges", "source": "hospital"}]}
    assert parts(corpus, BV, C_SECTION) == {HAMILTON: [{"max": None, "terms": "$1,800 a day", "source": "hospital"}]}
    corpus.build()
    price = {r[:3]: r for r in corpus.rows("prices")}[(PATHWAY, VAGINAL, GRADY)]
    assert price[4] is False  # one rate


def test_a_stay_the_plans_file_calls_outpatient_is_still_priced(corpus):
    # An MS-DRG pays only for a stay, whatever setting the file gives it.
    stays(corpus)
    drg(corpus, 50, "807", 5200, setting="outpatient")
    assert parts(corpus, PATHWAY, VAGINAL) == {GRADY: [{"max": 5200.0, "terms": None, "source": "plan"}]}


def test_a_derived_estimate_and_a_bundle_are_left_out(corpus):
    stays(corpus)
    drg(corpus, 50, "788", 9000, negotiated_type="derived")
    drg(corpus, 50, "788", 3962, arrangement="bundle")
    drg(corpus, 50, "788", 1, negotiated_type="negotiated")
    assert stay_prices(corpus, PATHWAY, C_SECTION) == {}


def test_a_drg_never_matches_another_code_types_code_of_the_same_digits(corpus):
    stays(corpus)
    corpus.facility_price(PATHWAY, 50, "470", 8000, setting="inpatient")  # a CPT line for "470"
    corpus.facility_price(PATHWAY, 50, "470", 8100, setting="inpatient", code_type="RC")
    corpus.hospital_price(GRADY, "470", "ANTHEM BCBS", "PATHWAY", negotiated=7000.0, setting="inpatient")
    corpus.hospital_price(GRADY, "470", "ANTHEM BCBS", "PATHWAY", negotiated=7100.0, setting="inpatient",
                          code_type="RC")
    assert stay_prices(corpus, PATHWAY, KNEE) == {}
    assert stay_prices(corpus, PATHWAY, HIP) == {}
    drg(corpus, 50, "470", 21000)
    assert parts(corpus, PATHWAY, KNEE) == parts(corpus, PATHWAY, HIP) == {
        GRADY: [{"max": 21000.0, "terms": None, "source": "plan"}]}


def test_a_cpt_service_never_matches_a_drg_of_the_same_digits(corpus):
    stays(corpus)
    corpus.hospital_price(GRADY, "72148", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=1111.0, code_type="MS-DRG",
                          setting="inpatient")
    assert corpus.inputs().execute("SELECT * FROM facility_fees WHERE service_id = 'mri-lower-back'").fetchall() == []


def test_the_doctor_is_an_extra_bill_from_the_services_doctor_code_and_anesthesia_at_any_setting(corpus):
    stays(corpus)
    drg(corpus, 50, "807", 5000)
    drg(corpus, 50, "470", 21000)
    for rate in (900, 1000, 1100, 1.0, 20):  # a placeholder and a unit count are dropped
        corpus.extra_price(PATHWAY, "59400", rate, setting="both")
    corpus.extra_price(PATHWAY, "59400", 5000, modifier="22", setting="both")
    corpus.extra_price(PATHWAY, "01967", 400, setting="outpatient")
    corpus.extra_price(PATHWAY, "01967", 500, setting="inpatient")
    corpus.extra_price(BV, "27447", 3000, setting="inpatient")
    corpus.build()
    extra = {(r[0], r[1], r[2]): r[3:] for r in corpus.rows("extra_bills")}
    assert extra[(PATHWAY, VAGINAL, "doctor")] == ("59400", 920.0, 1000.0, 1080.0, 3)
    assert extra[(PATHWAY, VAGINAL, "anesthesia")] == ("01967", 410.0, 450.0, 490.0, 2)
    assert extra[(BV, KNEE, "doctor")] == ("27447", 3000.0, 3000.0, 3000.0, 1)
    assert (BV, VAGINAL, "doctor") not in extra


def test_the_family_says_what_to_show_when_no_hospital_has_a_price(corpus):
    stays(corpus)
    drg(corpus, 50, "807", 5000)
    corpus.build()
    assert {r[0]: r[4:] for r in corpus.rows("families")}["stays"][-1] == "No hospital shows a price for a stay."


@pytest.mark.parametrize("change, error", [
    ({"code_type": "ICD"}, r"stay-knee-replacement's code_type 'ICD' isn't one of \['MS-DRG'\]"),
    ({"billing_code": "47"}, r"stay-knee-replacement's billing_code '47' isn't a three-digit MS-DRG"),
    ({"code_type": None, "billing_code": "27447"}, r"stay-knee-replacement is in stays, which names no groups or codes"),
    ({"doctor": "27130"}, r"\['stay-hip-replacement', 'stay-knee-replacement'\] all bill 470 in stays"),
    ({"doctor": None}, r"\['stay-hip-replacement', 'stay-knee-replacement'\] all bill 470 in stays"),
    ({"doctor": "2744"}, r"stay-knee-replacement's doctor code '2744' isn't a five-character code"),
])
def test_a_stay_that_breaks_a_rule_stops_the_build(corpus, change, error):
    stays(corpus)
    corpus.services = [(f, {**s, **change} if s["service_id"] == KNEE else s) for f, s in corpus.services]
    corpus.write_services()
    with pytest.raises(SystemExit, match=error):
        corpus.build()


def test_a_family_with_a_blank_empty_note_stops_the_build(corpus):
    stays(corpus)
    corpus.family("stays", empty_note=" ")
    try:
        with pytest.raises(SystemExit, match="stays's empty_note must be quoted text"):
            corpus.build()
    finally:  # corpus.family edits the shared module-level family, so put it back
        corpus.family("stays", empty_note="No hospital shows a price for a stay.")


def test_one_amount_over_many_drgs_is_a_default_not_a_stay_price(corpus):
    stays(corpus)
    for code in range(100, 126):  # 26 DRGs at one amount: a default
        drg(corpus, 50, str(code), 2045.79)
    drg(corpus, 50, "807", 2045.79)
    corpus.hospital_price(GRADY, "807", "ANTHEM BCBS", "PATHWAY", negotiated=9000.0, setting="inpatient",
                          code_type="MS-DRG")
    assert parts(corpus, PATHWAY, VAGINAL) == {GRADY: [{"max": 9000.0, "terms": None, "source": "hospital"}]}


def test_one_amount_over_six_delivery_drgs_is_a_tier_and_kept(corpus):
    stays(corpus)
    for code in ("796", "797", "798", "805", "806", "807"):
        drg(corpus, 50, code, 9268.87)
    assert parts(corpus, PATHWAY, VAGINAL) == {GRADY: [{"max": 9268.87, "terms": None, "source": "plan"}]}


def test_a_hospitals_own_file_default_over_many_drgs_is_left_out(corpus):
    stays(corpus)
    for code in range(100, 127):
        corpus.hospital_price(HAMILTON, str(code), "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=2045.79,
                              setting="inpatient", code_type="MS-DRG")
    corpus.hospital_price(HAMILTON, "807", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=2045.79, setting="inpatient",
                          code_type="MS-DRG")
    assert parts(corpus, BV, VAGINAL) == {}
