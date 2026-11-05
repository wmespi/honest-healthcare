"""Build-only: the place each biller in biller_rates is, if it has one."""
from build.inputs.datasets import DOCTORS_CLINICIANS, GEOCODES, NPPES
from build.table import Table

# USPS's standard abbreviations (Publication 28) for the street words addresses
# spell out, so "1200 MEMORIAL DRIVE" and "1200 MEMORIAL DR" are one street.
STREET_WORDS = {"STREET": "ST", "DRIVE": "DR", "ROAD": "RD", "AVENUE": "AVE", "BOULEVARD": "BLVD",
                "HIGHWAY": "HWY", "PARKWAY": "PKWY", "EXPRESSWAY": "EXPY", "CIRCLE": "CIR",
                "COURT": "CT", "LANE": "LN", "PLACE": "PL", "POINT": "PT", "TRAIL": "TRL",
                "TERRACE": "TER", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
                "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW"}

# Company endings a practice's billing IDs spell differently, so "MIDTOWN IMAGING,
# P.C." and "MIDTOWN IMAGING PC" are one practice.
ENDINGS = ["LLC", "PLLC", "LLP", "PC", "PA", "INC", "LTD", "CORP", "CORPORATION", "CO", "THE"]


def _words(text):
    """SQL for `text`'s words in capitals, without punctuation: "P.C." is "PC" and
    "EAGLE'S" is "EAGLES"."""
    return (f"list_filter(string_split(regexp_replace(regexp_replace(upper({text}), '[.'']', '', 'g'), "
            f"'[^A-Z0-9]+', ' ', 'g'), ' '), w -> w != '')")


def street(line):
    """SQL for an address line's street, in USPS's abbreviations and without the
    cross street of an address like "915 GORDON AVENUE & MIMOSA DRIVE"."""
    words = ", ".join(f"'{word}': '{short}'" for word, short in STREET_WORDS.items())
    before_cross_street = f"split_part({line}, '&', 1)"
    return (f"array_to_string(list_transform({_words(before_cross_street)}, "
            f"w -> coalesce(MAP {{{words}}}[w], w)), ' ')")


def practice_name(name):
    """SQL for a practice's name without its punctuation or company endings."""
    endings = ", ".join(f"'{e}'" for e in ENDINGS)
    return f"array_to_string(list_filter({_words(name)}, w -> w NOT IN ({endings})), ' ')"


class BillerPlaces(Table):
    """A place's name and address come from the biller's own NPPES record, never
    its roster. Two addresses are one building when Census standardized them to
    the same address, or when their streets and ZIPs match once spelled the same
    way; an address Census didn't match takes the Census address and point of
    another on its street. A point is only ever Census's, so a place in a
    building Census matched no address in has none.

    A biller in a Georgia hospital's building is that hospital, keyed on its CCN.
    Any other biller belongs to a practice: the billers in one building whose
    names match once punctuation and company endings are dropped. A practice is
    keyed on its lowest billing NPI and takes that biller's name and address, and
    it's an imaging center if NPPES registers any of its billers as one.
    Different practices in one building stay apart, because their prices differ
    more often than one practice's billing IDs do. A doctor's own NPI takes the
    name of its CMS group, and away from a hospital it has no place without one.
    A biller with no Georgia NPPES record has no place either (#135 step 8).

    `biller_name` is the biller's own name, which a hospital's row lists for the
    groups reading there: its NPPES name, or for a doctor's own NPI its CMS
    group's, or the doctor's when it has none."""
    name = "biller_places"
    key = ["biller"]
    columns = {"biller": "VARCHAR", "biller_name": "VARCHAR", "place_id": "VARCHAR", "kind": "VARCHAR",
               "name": "VARCHAR", "address": "VARCHAR", "city": "VARCHAR", "zip": "VARCHAR",
               "lat": "DOUBLE", "lon": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            WITH listed AS (
                SELECT b.biller, n.taxonomy_code IN (SELECT taxonomy_code FROM imaging_centers) AS center,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name ELSE d.org_name END AS name,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name
                            ELSE coalesce(d.org_name, concat_ws(' ', n.first_name, n.last_name)) END AS biller_name,
                       concat_ws(', ', n.address_line1, nullif(trim(n.address_line2), '')) AS address,
                       n.city, left(n.postal_code, 5) AS zip,
                       {street("n.address_line1")} || ' ' || left(n.postal_code, 5) AS street,
                       g.matched_address, g.latitude AS lat, g.longitude AS lon
                FROM (SELECT DISTINCT biller FROM biller_rates) b
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(b.biller AS BIGINT)
                LEFT JOIN {DOCTORS_CLINICIANS.read(data_dir)} d ON d.npi = n.npi
                LEFT JOIN {GEOCODES.read(data_dir)} g
                  ON g.address_line1 = n.address_line1 AND coalesce(g.address_line2, '') = coalesce(n.address_line2, '')
                 AND g.city = n.city AND g.state = n.state AND g.postal_code = n.postal_code
            ),
            streets AS (
                SELECT DISTINCT ON (street) street, matched_address, lat, lon
                FROM listed WHERE matched_address IS NOT NULL
                ORDER BY street, matched_address, lat, lon
            ),
            own AS (
                SELECT l.biller, l.biller_name, l.center, l.name, l.address, l.city, l.zip, l.street,
                       coalesce(l.matched_address, s.matched_address) AS matched_address,
                       coalesce(l.lat, s.lat) AS lat, coalesce(l.lon, s.lon) AS lon
                FROM listed l LEFT JOIN streets s USING (street)
            ),
            at_hospital AS (
                SELECT o.biller, o.biller_name, h.ccn, h.name, h.address, h.city, left(h.postal_code, 5) AS zip,
                       h.lat, h.lon
                FROM own o JOIN hospitals h
                  ON o.matched_address = h.matched_address
                  OR o.street = {street("h.address")} || ' ' || left(h.postal_code, 5)
            ),
            practices AS (
                SELECT biller, biller_name, min(biller) OVER practice AS place_id,
                       bool_or(center) OVER practice AS any_center
                FROM own
                WHERE name IS NOT NULL AND biller NOT IN (SELECT biller FROM at_hospital)
                WINDOW practice AS (PARTITION BY coalesce(matched_address, street), {practice_name("name")})
            )
            SELECT biller, biller_name, ccn AS place_id, 'hospital' AS kind, name, address, city, zip, lat, lon
            FROM at_hospital
            UNION ALL
            SELECT p.biller, p.biller_name, p.place_id, CASE WHEN p.any_center THEN 'imaging_center' ELSE 'practice' END,
                   o.name, o.address, o.city, o.zip, o.lat, o.lon
            FROM practices p JOIN own o ON o.biller = p.place_id
        """

    def check(self, con, data_dir):
        """A biller with no name of its own stops the build, since a hospital's row
        couldn't say who bills there.

        Not a stop: list every billing ID with a price but no place, so an NPI
        missing from this month's NPPES snapshot doesn't drop out silently, and
        every hospital's own billing ID that landed as a practice, because its
        address matches no hospital's."""
        nameless = con.execute("SELECT biller FROM biller_places WHERE biller_name IS NULL LIMIT 1").fetchone()
        if nameless:
            raise SystemExit(f"biller_places: billing ID {nameless[0]} has no name in NPPES or CMS")
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
        hospitals = [f"{r[0]} ({r[1]})" for r in con.execute(f"""
            SELECT p.biller, p.name FROM biller_places p
            JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(p.biller AS BIGINT)
            WHERE p.kind != 'hospital' AND n.is_hospital ORDER BY p.biller
        """).fetchall()]
        if hospitals:
            print(f"  billing IDs NPPES registers as hospitals that match no hospital's address, so they're "
                  f"practices ({len(hospitals)}): {', '.join(hospitals)}")
