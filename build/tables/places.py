"""places.parquet: every Georgia hospital and every practice with a price that
Census can put on the map, and unlocated_places.parquet: the ones it can't."""
from build.table import Table

# Every hospital, and every practice with a price (build/inputs/biller_places.py).
EVERY_PLACE = """
    SELECT ccn AS place_id, name, 'hospital' AS kind, address, city, left(postal_code, 5) AS zip,
           lat, lon, phone, stars
    FROM hospitals
    UNION ALL
    SELECT DISTINCT place_id, name, kind, address, city, zip, lat, lon, NULL::VARCHAR, NULL::INTEGER
    FROM biller_places WHERE kind != 'hospital'
"""


class Places(Table):
    """A hospital is keyed on its CCN and has Medicare's star rating and its phone.
    Any other place is a practice with a price: its billers at one address under
    one name, keyed on its lowest billing NPI (build/inputs/biller_places.py).
    Every place has Census's point on the map. One Census can't place isn't
    shown, because any other point would be a guess, and goes to
    unlocated_places instead."""
    name = "places"
    key = ["place_id"]
    columns = {"place_id": "VARCHAR", "name": "VARCHAR", "kind": "VARCHAR", "address": "VARCHAR",
               "city": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE", "phone": "VARCHAR",
               "stars": "INTEGER"}

    def select(self, data_dir):
        return f"SELECT * EXCLUDE (zip) FROM ({EVERY_PLACE}) WHERE lat IS NOT NULL AND lon IS NOT NULL"

    def check(self, con, data_dir):
        bad = con.execute("""
            SELECT place_id, stars FROM places
            WHERE stars IS NOT NULL AND (kind != 'hospital' OR stars NOT BETWEEN 1 AND 5)
            ORDER BY place_id
        """).fetchone()
        if bad:
            raise SystemExit(f"places: {bad[0]} has {bad[1]} stars, but only a hospital is rated, 1 to 5")


class UnlocatedPlaces(Table):
    """The places Census can't put on the map, left off the screen and saved with
    the address and ZIP to try with another geocoder. Nothing serves this table."""
    name = "unlocated_places"
    key = ["place_id"]
    columns = {"place_id": "VARCHAR", "name": "VARCHAR", "kind": "VARCHAR", "address": "VARCHAR",
               "city": "VARCHAR", "zip": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT place_id, name, kind, address, city, zip FROM ({EVERY_PLACE})
            WHERE lat IS NULL OR lon IS NULL
        """
