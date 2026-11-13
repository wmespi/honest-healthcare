"""The build-only hospital_plans table (integration/inputs/hospital_plans.py): what
each payer and plan name in a hospital's own price file means, from the reviewed
list in integration/hospital_plans.yaml."""
import duckdb
import pytest

from integration.inputs.hospital_plans import HOSPITAL_PLANS_PATH, HospitalPlans, read_hospital_plans

BV, PATHWAY = "45334-GAN001", "49046-GAN008"
GRADY, HAMILTON = "110079", "110001"


def names(corpus):
    return corpus.inputs().execute("SELECT * FROM hospital_plans ORDER BY ccn, name").fetchall()


def test_each_hospital_in_a_group_takes_its_names(corpus):
    corpus.hospital_plans([{"hospitals": [GRADY, HAMILTON], "networks": {BV: ["ANTHEM BCBS | BLUE VALUE HMO"]},
                            "other": ["ANTHEM BCBS | MEDICARE ADVANTAGE"]}])
    assert names(corpus) == [
        (HAMILTON, "ANTHEM BCBS | BLUE VALUE HMO", BV), (HAMILTON, "ANTHEM BCBS | MEDICARE ADVANTAGE", None),
        (GRADY, "ANTHEM BCBS | BLUE VALUE HMO", BV), (GRADY, "ANTHEM BCBS | MEDICARE ADVANTAGE", None)]


def test_a_name_the_list_doesnt_give_a_hospital_stops_the_build_and_lists_every_one(corpus):
    # A new plan at Grady, a payer with no plan at Hamilton, and a name the list
    # gives Grady but not Hamilton. A row of charges alone has no name.
    corpus.hospital_price(GRADY, "72148", "ANTHEM BCBS", "INDIVIDUAL PPO", negotiated=900.0)
    corpus.hospital_price(HAMILTON, "72148", "BLUE CROSS", negotiated=950.0)
    corpus.hospital_price(HAMILTON, "72148", "ANTHEM BCBS", "BLUE VALUE HMO", negotiated=980.0)
    corpus.hospital_price(HAMILTON, "72148", cash=1200.0)
    with pytest.raises(SystemExit) as stop:
        corpus.build()
    assert str(stop.value).startswith("hospital_plans: hospitals' own price files use names")
    assert ("(3): 110001 'ANTHEM BCBS | BLUE VALUE HMO', 110001 'BLUE CROSS |', "
            "110079 'ANTHEM BCBS | INDIVIDUAL PPO'; list each") in str(stop.value)


@pytest.mark.parametrize("groups, error", [
    ([{"hospitals": [GRADY], "other": ["A | B"]}, {"hospitals": [GRADY], "other": ["C | D"]}],
     "hospital 110079 is in group 1 and group 2"),
    ([{"hospitals": [GRADY], "networks": {BV: ["A | B"]}, "other": ["A | B"]}],
     "group 1 lists 'A | B' twice for hospital 110079"),
    ([{"hospitals": [GRADY], "networks": {"58081-GAN001": ["A | B"]}}],
     "58081-GAN001 isn't a network the payer rates list"),
    ([{"hospitals": [], "other": ["A | B"]}], "group 1 lists no hospitals"),
    ([{"hospitals": [GRADY], "plans": ["A | B"]}], "group 1 has fields the build doesn't read: ['plans']"),
    ([{"hospitals": [GRADY], "networks": ["A | B"]}], "group 1's networks aren't each a network ID"),
    ({"hospitals": [GRADY]}, "isn't a list of groups"),
])
def test_a_list_the_build_cant_read_one_way_stops_it(corpus, groups, error):
    corpus.hospital_plans(groups)
    with pytest.raises(SystemExit, match=error.replace("[", r"\[").replace("|", r"\|")):
        corpus.build()


def test_the_reviewed_list_reads():
    """integration/hospital_plans.yaml itself."""
    assert isinstance(read_hospital_plans(), list)
    HospitalPlans(HOSPITAL_PLANS_PATH).fill(duckdb.connect(), data_dir=None)
