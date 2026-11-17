"""extra_bills.parquet (integration/tables/extra_bills.py) and the codes in services.yaml
that feed it."""
import pytest

from integration.tests.corpus import BLUE_VALUE, PATHWAY


def extra(corpus):
    return {(r[0], r[1], r[2]): r[3:] for r in corpus.rows("extra_bills")}


def test_a_mapped_service_gets_its_networks_range_from_no_modifier_fee_schedule_lines(corpus):
    corpus.build()
    # Pathway: the unit count (6), the 50 line and the percentage are left out.
    assert extra(corpus)[(PATHWAY, "mri-lower-back", "anesthesia")] == ("00811", 220.0, 300.0, 380.0, 5)
    # Blue Value: three lab lines, the 26 line left out, and no anesthesia row.
    assert extra(corpus)[(BLUE_VALUE, "mri-lower-back", "pathology")] == ("88305", 32.0, 40.0, 48.0, 3)
    assert (BLUE_VALUE, "mri-lower-back", "anesthesia") not in extra(corpus)
    assert (PATHWAY, "mri-lower-back", "pathology") not in extra(corpus)


def test_a_service_mapping_nothing_has_no_row(corpus):
    corpus.build()
    assert {k[1] for k in extra(corpus)} == {"mri-lower-back"}


@pytest.mark.parametrize("field, code, error", [
    ("anesthesia", "99213", "anesthesia code '99213' isn't a five-character code from 00100 to 01999"),
    ("anesthesia", "0081", "anesthesia code '0081'"),
    ("pathology", "883", "pathology code '883' isn't a five-character code"),
])
def test_a_bad_extra_bill_code_stops_the_build(corpus, field, code, error):
    corpus.service("mri-brain", "70551", **{field: code})
    with pytest.raises(SystemExit, match=error):
        corpus.build()
