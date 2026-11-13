"""Build-only: every address the build puts a biller, a hospital or a doctor at,
with the building it's in and the building's point on the map."""
from integration.inputs.datasets import CLINICIAN_LOCATIONS, GEOCODES, HOSPITALS, NPPES, NPPES_LOCATIONS
from integration.table import Table

# USPS's standard abbreviations (Publication 28) for the street words addresses
# spell out, so "1200 MEMORIAL DRIVE" and "1200 MEMORIAL DR" are one street.
STREET_WORDS = {"STREET": "ST", "DRIVE": "DR", "ROAD": "RD", "AVENUE": "AVE", "BOULEVARD": "BLVD",
                "HIGHWAY": "HWY", "PARKWAY": "PKWY", "EXPRESSWAY": "EXPY", "CIRCLE": "CIR",
                "COURT": "CT", "LANE": "LN", "PLACE": "PL", "POINT": "PT", "TRAIL": "TRL",
                "TERRACE": "TER", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
                "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW"}

# The words that start the part of an address line saying where in the building,
# so "100 MAIN ST STE 200" and "100 MAIN ST" are one street.
UNITS = ["STE", "SUITE", "UNIT", "BLDG", "BUILDING", "FL", "FLOOR", "RM", "ROOM", "APT"]

ADDRESS = "address_line1, address_line2, city, state, postal_code"


def _words(text):
    """SQL for `text`'s words in capitals, without punctuation: "P.C." is "PC" and
    "EAGLE'S" is "EAGLES"."""
    return (f"list_filter(string_split(regexp_replace(regexp_replace(upper({text}), '[.'']', '', 'g'), "
            f"'[^A-Z0-9]+', ' ', 'g'), ' '), w -> w != '')")


def listed(data_dir):
    """SQL for every Georgia address NPPES lists each NPI at, with the phone there:
    the one on its own record (`main`) and the other places it lists. An empty
    second line is ''."""
    return f"""(
        SELECT npi, true AS main, address_line1, coalesce(address_line2, '') AS address_line2, city, state,
               postal_code, phone
        FROM {NPPES.read(data_dir)}
        UNION ALL
        SELECT npi, false, address_line1, coalesce(address_line2, ''), city, state, postal_code, phone
        FROM {NPPES_LOCATIONS.read(data_dir)})"""


def street(line):
    """SQL for an address line's street: in USPS's abbreviations, without the
    cross street of "915 GORDON AVENUE & MIMOSA DRIVE", and without the suite of
    "100 MAIN ST STE 200" or "100 MAIN ST # 200"."""
    words = ", ".join(f"'{word}': '{short}'" for word, short in STREET_WORDS.items())
    before_unit_number = f"regexp_replace(split_part({line}, '&', 1), '^(.*?\\S.*?)\\s*#.*$', '\\1')"
    spelled = (f"array_to_string(list_transform({_words(before_unit_number)}, "
               f"w -> coalesce(MAP {{{words}}}[w], w)), ' ')")
    return f"regexp_replace({spelled}, '^(\\S+ \\S+.*?) (?:{'|'.join(UNITS)})(?: .*)?$', '\\1')"


class Addresses(Table):
    """One row per address: the one on the NPPES record of each biller in
    biller_rates and each hospital billing NPI in facility_rates, and the other
    places it lists, each hospital's, and each one CMS lists a doctor in
    biller_doctors practicing at.

    Two addresses are one `building` when Census standardized them to the same
    address, or when their streets and ZIPs match once spelled the same way and
    cut before any suite. An address Census didn't locate takes the Census
    address of another on its street, and one on a street Census located nothing
    on is known by the street and ZIP. Every address on a hospital's street is in
    the hospital's building.

    A building has one point: Census's for an address in it, the hospital's own
    first, or OpenStreetMap's when Census located none. `located_by` names which,
    and both are empty when neither located an address in the building, because
    any other point would be a guess (ingestion/reference/geocode.md)."""
    name = "addresses"
    key = ADDRESS.split(", ")
    columns = {"address_line1": "VARCHAR", "address_line2": "VARCHAR", "city": "VARCHAR", "state": "VARCHAR",
               "postal_code": "VARCHAR", "building": "VARCHAR", "lat": "DOUBLE", "lon": "DOUBLE",
               "located_by": "VARCHAR"}

    def select(self, data_dir):
        billers = ("(SELECT TRY_CAST(biller AS BIGINT) FROM biller_rates "
                   "UNION SELECT TRY_CAST(biller AS BIGINT) FROM facility_rates)")

        def answer(source, alias):
            return f"""
                LEFT JOIN {GEOCODES.read(data_dir)} {alias}
                  ON {alias}.source = '{source}' AND {alias}.located
                 AND {alias}.address_line1 = a.address_line1 AND {alias}.address_line2 = a.address_line2
                 AND {alias}.city = a.city AND {alias}.state = a.state AND {alias}.postal_code = a.postal_code"""

        return f"""
            WITH listed AS (
                SELECT {ADDRESS}, false AS hospital FROM {listed(data_dir)} WHERE npi IN {billers}
                UNION ALL
                SELECT address_line1, coalesce(address_line2, ''), city, state, postal_code, false
                FROM {CLINICIAN_LOCATIONS.read(data_dir)} WHERE npi IN (SELECT npi FROM biller_doctors)
                UNION ALL
                SELECT address, '', city, state, postal_code, true FROM {HOSPITALS.read(data_dir)}
            ),
            answered AS (
                SELECT a.*, {street("a.address_line1")} || ' ' || left(a.postal_code, 5) AS street,
                       c.matched_address, c.latitude AS census_lat, c.longitude AS census_lon,
                       o.latitude AS osm_lat, o.longitude AS osm_lon
                FROM (SELECT {ADDRESS}, bool_or(hospital) AS hospital FROM listed GROUP BY {ADDRESS}) a
                {answer("census", "c")}
                {answer("openstreetmap", "o")}
            ),
            standardized AS (
                SELECT *, coalesce(matched_address, min(matched_address) OVER (PARTITION BY street), street) AS own
                FROM answered
            ),
            housed AS (
                SELECT *, coalesce(min(own) FILTER (WHERE hospital) OVER (PARTITION BY street), own) AS building
                FROM standardized
            ),
            points AS (
                SELECT DISTINCT ON (building) building, lat, lon, located_by
                FROM (SELECT building, hospital, census_lat AS lat, census_lon AS lon, 'census' AS located_by
                      FROM housed WHERE census_lat IS NOT NULL
                      UNION ALL
                      SELECT building, hospital, osm_lat, osm_lon, 'openstreetmap'
                      FROM housed WHERE osm_lat IS NOT NULL)
                ORDER BY building, located_by != 'census', NOT hospital, lat, lon
            )
            SELECT {", ".join(f"h.{c}" for c in self.key)}, h.building, p.lat, p.lon, p.located_by
            FROM housed h LEFT JOIN points p USING (building)
        """

    def check(self, con, data_dir):
        """Not a stop: say how many buildings each geocoder located."""
        census, osm, neither = con.execute("""
            SELECT count(*) FILTER (WHERE located_by = 'census'), count(*) FILTER (WHERE located_by = 'openstreetmap'),
                   count(*) FILTER (WHERE located_by IS NULL)
            FROM (SELECT DISTINCT building, located_by FROM addresses)
        """).fetchone()
        print(f"  buildings: {census:,} located by Census, {osm:,} by OpenStreetMap, {neither:,} by neither")
