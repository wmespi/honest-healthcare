"""zips.parquet (build/tables/zips.py)."""
import pytest


def test_zips_are_every_census_zip_including_out_of_state(corpus):
    assert corpus.build()["zips"] == 3
    assert corpus.rows("zips") == [("30308", 33.770959, -84.377736),
                                   ("30322", 33.794605, -84.325247),
                                   ("37421", 35.030393, -85.159553)]


@pytest.mark.parametrize("zip_code, lat, lon", [
    ("30309", None, -84.38),   # the Census row's latitude didn't parse
    ("3030", 33.79, -84.38),   # a four-digit ZIP
    ("30310", 133.0, -84.38),  # off the map
])
def test_a_zip_without_a_valid_point_stops_the_build(corpus, zip_code, lat, lon):
    corpus.zip(zip_code, lat, lon)
    with pytest.raises(SystemExit, match=f"zips: '{zip_code}' isn't a five-digit ZIP"):
        corpus.build()
