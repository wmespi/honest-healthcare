"""places.parquet: every Georgia hospital, and every other place with a price."""
from build.table import Table


class Places(Table):
    """A hospital is keyed on its CCN and has Medicare's star rating and its phone.
    Any other place is a biller with a price, keyed on its NPI, at its own
    address (build/inputs/biller_places.py). Every place has a point on the map:
    Census's, or its ZIP's center."""
    name = "places"
    key = ["place_id"]
    columns = {"place_id": "VARCHAR", "name": "VARCHAR", "kind": "VARCHAR", "address": "VARCHAR",
               "city": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE", "phone": "VARCHAR",
               "stars": "INTEGER"}

    def select(self, data_dir):
        return """
            SELECT ccn AS place_id, name, 'hospital' AS kind, address, city, lat, lon, phone, stars
            FROM hospitals
            UNION ALL
            SELECT DISTINCT place_id, name, kind, address, city, lat, lon,
                   NULL::VARCHAR AS phone, NULL::INTEGER AS stars
            FROM biller_places WHERE kind != 'hospital'
        """

    def check(self, con, data_dir):
        lost = con.execute("""
            SELECT place_id, name FROM places
            WHERE lat IS NULL OR lon IS NULL ORDER BY place_id
        """).fetchone()
        if lost:
            raise SystemExit(f"places: {lost[0]} ({lost[1]}) has no point on the map: Census didn't "
                             f"match its address and its ZIP has no centroid")
        bad = con.execute("""
            SELECT place_id, stars FROM places
            WHERE stars IS NOT NULL AND (kind != 'hospital' OR stars NOT BETWEEN 1 AND 5)
            ORDER BY place_id
        """).fetchone()
        if bad:
            raise SystemExit(f"places: {bad[0]} has {bad[1]} stars, but only a hospital is rated, 1 to 5")
