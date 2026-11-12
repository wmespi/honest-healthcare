"""Build-only: each Georgia hospital, with its building and its point on the map."""
from integration.inputs.datasets import HOSPITALS
from integration.table import Table


class Hospitals(Table):
    """`building` is the building the hospital's address is in, which the billers
    there share, and the point is the building's (integration/inputs/addresses.py).
    The point is empty when no geocoder located an address in the building."""
    name = "hospitals"
    key = ["ccn"]
    columns = {"ccn": "VARCHAR", "name": "VARCHAR", "address": "VARCHAR", "city": "VARCHAR",
               "postal_code": "VARCHAR", "phone": "VARCHAR", "stars": "INTEGER",
               "building": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT h.ccn, h.name, h.address, h.city, h.postal_code, h.phone, h.stars,
                   a.building, a.lat, a.lon
            FROM {HOSPITALS.read(data_dir)} h
            JOIN addresses a
              ON a.address_line1 = h.address AND a.address_line2 = ''
             AND a.city = h.city AND a.state = h.state AND a.postal_code = h.postal_code
        """
