"""premiums.parquet (integration/tables/premiums.py)."""
import pytest


def test_premiums_are_every_age_where_each_plan_is_sold(corpus):
    # Three plans in rating area 1, one of them in area 2 too; the untargeted
    # network's plan is left out.
    assert corpus.build()["premiums"] == 4 * 65
    rows = corpus.rows("premiums")
    assert rows[0] == ("45334GA0020002", 1, 0, 300.0, 330.0)
    assert ("45334GA0020002", 2, 64, 940.0, 1034.0) in rows
    assert {r[0] for r in rows} == {"45334GA0020002", "45334GA0020008", "49046GA0700053"}


def test_a_plan_with_no_premiums_stops_the_build(corpus):
    corpus.remove_premiums("45334GA0020008")
    with pytest.raises(SystemExit, match="no premium for 45334GA0020008"):
        corpus.build()


def test_a_missing_age_stops_the_build(corpus):
    corpus.remove_premiums("49046GA0700053", ages=[30])
    with pytest.raises(SystemExit, match="49046GA0700053 in rating area 1 has 64 ages, "
                                         "not every age from 0 to 64"):
        corpus.build()
