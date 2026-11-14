"""places.parquet: every Georgia hospital and every practice with a price that
a geocoder can put on the map, and unlocated_places.parquet: the ones none can."""
from integration.table import Table

# Every hospital, and every practice a service is shown at (integration/inputs/service_sites.py).
EVERY_PLACE = """
    SELECT ccn AS place_id, name, 'hospital' AS kind, address, city, left(postal_code, 5) AS zip,
           lat, lon, phone, stars
    FROM hospitals
    UNION ALL
    SELECT DISTINCT place_id, name, kind, address, city, zip, lat, lon, phone, NULL::INTEGER
    FROM biller_places
    WHERE kind != 'hospital' AND (biller, place_id) IN (SELECT (biller, place_id) FROM service_sites)
"""


class Places(Table):
    """A hospital is keyed on its CCN and has Medicare's star rating and its phone.
    Any other place is a practice with a price: its billers in one building under
    one name, with the phone NPPES lists there (integration/inputs/biller_places.py).
    `kind_name` is what the screen calls its kind (integration/families.yaml).
    Every place has its building's point on the map (integration/inputs/addresses.py).
    One in a building no geocoder located isn't shown, because any other point
    would be a guess, and goes to unlocated_places instead."""
    name = "places"
    key = ["place_id"]
    columns = {"place_id": "VARCHAR", "name": "VARCHAR", "kind": "VARCHAR", "kind_name": "VARCHAR",
               "address": "VARCHAR", "city": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE", "phone": "VARCHAR",
               "stars": "INTEGER"}

    def select(self, data_dir):
        return f"""
            SELECT p.place_id, p.name, p.kind, k.name AS kind_name, p.address, p.city, p.lat, p.lon, p.phone, p.stars
            FROM ({EVERY_PLACE}) p
            LEFT JOIN kinds k USING (kind)
            WHERE p.lat IS NOT NULL AND p.lon IS NOT NULL
        """

    def check(self, con, data_dir):
        bad = con.execute("""
            SELECT place_id, stars FROM places
            WHERE stars IS NOT NULL AND (kind != 'hospital' OR stars NOT BETWEEN 1 AND 5)
            ORDER BY place_id
        """).fetchone()
        if bad:
            raise SystemExit(f"places: {bad[0]} has {bad[1]} stars, but only a hospital is rated, 1 to 5")


class UnlocatedPlaces(Table):
    """The places no geocoder can put on the map, left off the screen and saved
    with the address and ZIP to look up by hand. Nothing serves this table."""
    name = "unlocated_places"
    key = ["place_id"]
    columns = {"place_id": "VARCHAR", "name": "VARCHAR", "kind": "VARCHAR", "address": "VARCHAR",
               "city": "VARCHAR", "zip": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT place_id, name, kind, address, city, zip FROM ({EVERY_PLACE})
            WHERE lat IS NULL OR lon IS NULL
        """
