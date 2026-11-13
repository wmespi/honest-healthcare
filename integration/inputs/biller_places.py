"""Build-only: the places each biller in biller_rates is at, if it has any."""
from integration.inputs.addresses import ADDRESS, _words, listed
from integration.inputs.datasets import DOCTORS_CLINICIANS, NPPES
from integration.table import Table

# Company endings a practice's billing IDs spell differently, so "MIDTOWN IMAGING,
# P.C." and "MIDTOWN IMAGING PC" are one practice.
ENDINGS = ["LLC", "PLLC", "LLP", "PC", "PA", "INC", "LTD", "CORP", "CORPORATION", "CO", "THE"]


def practice_name(name):
    """SQL for a practice's name without its punctuation or company endings."""
    endings = ", ".join(f"'{e}'" for e in ENDINGS)
    return f"array_to_string(list_filter({_words(name)}, w -> w NOT IN ({endings})), ' ')"


def _phone(digits):
    """SQL for a 10-digit phone number the way hospitals' are written,
    "(706) 272-6105", and nothing for any other."""
    return (f"CASE WHEN regexp_full_match({digits}, '[0-9]{{10}}') THEN '(' || substr({digits}, 1, 3) || ') ' "
            f"|| substr({digits}, 4, 3) || '-' || substr({digits}, 7, 4) END")


class BillerPlaces(Table):
    """One row per biller and place it's at. A biller is at the address on its
    NPPES record (`main`) and at the other Georgia addresses it lists there,
    never its roster's, and several of those in one building are one site
    (integration/inputs/addresses.py). service_sites says which of a biller's
    sites show its rate for a service.

    A site in a Georgia hospital's building is that hospital, keyed on its CCN.
    Any other site belongs to a practice: the billers with a site in the building
    whose names match once punctuation and company endings are dropped. A
    practice is keyed on the lowest billing NPI whose main address is in the
    building, and takes that biller's name, address and phone. Where none of its
    billers has its main address, the key is the lowest billing NPI and a short
    hash of the building, so one biller's sites are different places. It's an
    imaging center if NPPES registers any of its billers as one. Different
    practices in one building stay apart, because their prices differ more often
    than one practice's billing IDs do. A doctor's own NPI takes the name of its
    CMS group, and away from a hospital it has no place without one. A biller with
    no Georgia NPPES record has no place either (#135 step 8).

    The point is the building's, so a place in a building no geocoder located
    has none. `biller_name` is the biller's own name, which a hospital's row
    lists for the groups reading there: its NPPES name, or for a doctor's own NPI
    its CMS group's, or the doctor's when it has none."""
    name = "biller_places"
    key = ["biller", "place_id"]
    columns = {"biller": "VARCHAR", "biller_name": "VARCHAR", "place_id": "VARCHAR", "kind": "VARCHAR",
               "name": "VARCHAR", "address": "VARCHAR", "city": "VARCHAR", "zip": "VARCHAR",
               "lat": "DOUBLE", "lon": "DOUBLE", "phone": "VARCHAR", "main": "BOOLEAN", "building": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH billers AS (
                SELECT b.biller, n.npi, n.taxonomy_code IN (SELECT taxonomy_code FROM imaging_centers) AS center,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name ELSE d.org_name END AS name,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name
                            ELSE coalesce(d.org_name, concat_ws(' ', n.first_name, n.last_name)) END AS biller_name
                FROM (SELECT DISTINCT biller FROM biller_rates) b
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(b.biller AS BIGINT)
                LEFT JOIN {DOCTORS_CLINICIANS.read(data_dir)} d ON d.npi = n.npi
            ),
            listed AS {listed(data_dir)},
            sites AS (
                SELECT DISTINCT ON (b.biller, a.building)
                       b.biller, b.biller_name, b.name, b.center, l.main, a.building, a.lat, a.lon,
                       concat_ws(', ', l.address_line1, nullif(trim(l.address_line2), '')) AS address,
                       l.city, left(l.postal_code, 5) AS zip, {_phone("l.phone")} AS phone
                FROM billers b
                JOIN listed l USING (npi)
                JOIN addresses a USING ({ADDRESS})
                ORDER BY b.biller, a.building, NOT l.main, {", ".join(f"l.{c}" for c in ADDRESS.split(", "))}
            ),
            practices AS (
                SELECT biller, biller_name, main, building,
                       coalesce(min(biller) FILTER (WHERE main) OVER practice, min(biller) OVER practice) AS lowest,
                       bool_or(main) OVER practice AS any_main,
                       bool_or(center) OVER practice AS any_center
                FROM sites
                WHERE name IS NOT NULL AND building NOT IN (SELECT building FROM hospitals)
                WINDOW practice AS (PARTITION BY building, {practice_name("name")})
            )
            SELECT s.biller, s.biller_name, h.ccn AS place_id, 'hospital' AS kind, h.name, h.address, h.city,
                   left(h.postal_code, 5) AS zip, h.lat, h.lon, h.phone, s.main, s.building
            FROM sites s JOIN hospitals h USING (building)
            UNION ALL
            SELECT p.biller, p.biller_name,
                   CASE WHEN p.any_main THEN p.lowest ELSE p.lowest || '-' || left(md5(p.building), 8) END,
                   CASE WHEN p.any_center THEN 'imaging_center' ELSE 'practice' END,
                   o.name, o.address, o.city, o.zip, o.lat, o.lon, o.phone, p.main, p.building
            FROM practices p JOIN sites o ON o.biller = p.lowest AND o.building = p.building
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
            SELECT DISTINCT p.biller, p.name FROM biller_places p
            JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(p.biller AS BIGINT)
            WHERE p.main AND p.kind != 'hospital' AND n.is_hospital ORDER BY p.biller
        """).fetchall()]
        if hospitals:
            print(f"  billing IDs NPPES registers as hospitals that match no hospital's address, so they're "
                  f"practices ({len(hospitals)}): {', '.join(hospitals)}")
