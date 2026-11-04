"""zips.parquet: each ZIP's Census centroid, the point a ZIP search measures
distance from."""
from build.inputs.datasets import ZIP_CENTROIDS
from build.table import Table


class Zips(Table):
    """Every ZIP the Census lists, not only Georgia's: the file has no state, and
    someone near the state line may search from across it. A ZIP is five digits
    and has a point on the map."""
    name = "zips"
    key = ["zip"]
    columns = {"zip": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE"}

    def select(self, data_dir):
        return f"SELECT zip, lat, lon FROM {ZIP_CENTROIDS.read(data_dir)}"

    def check(self, con, data_dir):
        bad = con.execute("""
            SELECT zip, lat, lon FROM zips
            WHERE NOT regexp_full_match(zip, '[0-9]{5}')
               OR lat IS NULL OR lon IS NULL
               OR lat NOT BETWEEN -90 AND 90 OR lon NOT BETWEEN -180 AND 180
            ORDER BY zip
        """).fetchone()
        if bad:
            raise SystemExit(f"zips: {bad[0]!r} isn't a five-digit ZIP with a point on the map "
                             f"(lat {bad[1]}, lon {bad[2]})")
