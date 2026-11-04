"""Build-only: each Georgia hospital, with its standardized address and a point
on the map."""
from build.inputs.datasets import GEOCODES, HOSPITALS, ZIP_CENTROIDS
from build.table import Table


class Hospitals(Table):
    """`matched_address` is Census's standardized form of the hospital's address,
    which billers at the same address share, and it's empty when Census didn't
    match it. The point is Census's, or the ZIP's center when Census didn't
    match the address."""
    name = "hospitals"
    key = ["ccn"]
    columns = {"ccn": "VARCHAR", "name": "VARCHAR", "address": "VARCHAR", "city": "VARCHAR",
               "postal_code": "VARCHAR", "phone": "VARCHAR", "stars": "INTEGER",
               "matched_address": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT h.ccn, h.name, h.address, h.city, h.postal_code, h.phone, h.stars,
                   g.matched_address, coalesce(g.latitude, z.lat) AS lat, coalesce(g.longitude, z.lon) AS lon
            FROM {HOSPITALS.read(data_dir)} h
            LEFT JOIN {GEOCODES.read(data_dir)} g
              ON g.address_line1 = h.address AND coalesce(g.address_line2, '') = ''
             AND g.city = h.city AND g.state = h.state AND g.postal_code = h.postal_code
            LEFT JOIN {ZIP_CENTROIDS.read(data_dir)} z ON z.zip = left(h.postal_code, 5)
        """
