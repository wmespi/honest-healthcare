"""Build-only: the place each biller in biller_rates is, if it has one."""
from build.inputs.datasets import DOCTORS_CLINICIANS, GEOCODES, NPPES, ZIP_CENTROIDS
from build.table import Table


class BillerPlaces(Table):
    """A place's name and address come from the biller's own NPPES record, never
    its roster. A biller at a Georgia hospital's address is that hospital, keyed on
    its CCN: the two match on Census's standardized address, or on the street and
    ZIP when Census matched neither. Any other biller is its own place, keyed on
    its NPI, an imaging center if NPPES registers it as one and a practice
    otherwise. A doctor's own NPI takes the name of its CMS group, and away from a
    hospital it has no place without one. A biller with no Georgia NPPES record has
    no place either (#135 step 8)."""
    name = "biller_places"
    key = ["biller"]
    columns = {"biller": "VARCHAR", "place_id": "VARCHAR", "kind": "VARCHAR", "name": "VARCHAR",
               "address": "VARCHAR", "city": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            WITH own AS (
                SELECT b.biller, n.taxonomy_code,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name ELSE d.org_name END AS name,
                       concat_ws(', ', n.address_line1, nullif(trim(n.address_line2), '')) AS address,
                       n.address_line1, n.city, n.postal_code,
                       g.matched_address, coalesce(g.latitude, z.lat) AS lat, coalesce(g.longitude, z.lon) AS lon
                FROM (SELECT DISTINCT biller FROM biller_rates) b
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(b.biller AS BIGINT)
                LEFT JOIN {DOCTORS_CLINICIANS.read(data_dir)} d ON d.npi = n.npi
                LEFT JOIN {GEOCODES.read(data_dir)} g
                  ON g.address_line1 = n.address_line1 AND coalesce(g.address_line2, '') = coalesce(n.address_line2, '')
                 AND g.city = n.city AND g.state = n.state AND g.postal_code = n.postal_code
                LEFT JOIN {ZIP_CENTROIDS.read(data_dir)} z ON z.zip = left(n.postal_code, 5)
            ),
            at_hospital AS (
                SELECT o.biller, h.ccn, h.name, h.address, h.city, h.lat, h.lon
                FROM own o JOIN hospitals h
                  ON o.matched_address = h.matched_address
                  OR (o.matched_address IS NULL AND h.matched_address IS NULL
                      AND upper(trim(o.address_line1)) = upper(trim(h.address))
                      AND left(o.postal_code, 5) = left(h.postal_code, 5))
            )
            SELECT o.biller,
                   coalesce(h.ccn, o.biller) AS place_id,
                   CASE WHEN h.ccn IS NOT NULL THEN 'hospital'
                        WHEN o.taxonomy_code IN (SELECT taxonomy_code FROM imaging_centers) THEN 'imaging_center'
                        ELSE 'practice' END AS kind,
                   coalesce(h.name, o.name) AS name, coalesce(h.address, o.address) AS address,
                   coalesce(h.city, o.city) AS city, coalesce(h.lat, o.lat) AS lat, coalesce(h.lon, o.lon) AS lon
            FROM own o LEFT JOIN at_hospital h USING (biller)
            WHERE h.ccn IS NOT NULL OR o.name IS NOT NULL
        """

    def check(self, con, data_dir):
        """Not a stop: list every billing ID with a price but no place, so an NPI
        missing from this month's NPPES snapshot doesn't drop out silently."""
        for why, in_nppes in [("have no Georgia NPPES record", "NOT EXISTS"),
                              ("are a doctor's own NPI with no CMS group", "EXISTS")]:
            ids = [r[0] for r in con.execute(f"""
                SELECT b.biller FROM (SELECT DISTINCT biller FROM biller_rates) b
                WHERE b.biller NOT IN (SELECT biller FROM biller_places)
                  AND {in_nppes} (SELECT 1 FROM {NPPES.read(data_dir)} n
                                  WHERE n.npi = TRY_CAST(b.biller AS BIGINT))
                ORDER BY b.biller
            """).fetchall()]
            if ids:
                print(f"  billing IDs with a price that {why}, so they aren't places ({len(ids)}): "
                      f"{', '.join(ids)}")

