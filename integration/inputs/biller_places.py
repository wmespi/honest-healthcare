"""Build-only: the places each biller in biller_rates, and each center with a facility
fee in facility_rates, is at, if it has any."""
from integration.inputs.addresses import ADDRESS, _words, listed, suite
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
    """One row per biller and place it's at, for each biller with a rate in
    biller_rates and each center billing its own fee (facility_rates). A biller is at the address on its
    NPPES record (`main`) and at the other Georgia addresses it lists there,
    never its roster's, and several of those in one building are one site
    (integration/inputs/addresses.py). service_sites says which of a biller's
    sites show its rate for a service.

    A site in a Georgia hospital's building is that hospital, keyed on its CCN.
    Any other site belongs to a practice: the billers with a site in the building
    whose names match once punctuation and company endings are dropped, or who
    are at the same office (the same suite, or none listed), and the billers
    linked to those through others. Related companies often bill from one
    office under different names. A practice is keyed on the lowest billing NPI
    whose main address is in the building, and takes that biller's address and
    phone, and the name of the one with rates for the most services, the lowest
    NPI where two tie. Where none of its billers has its main address, the key is the
    lowest billing NPI and a short hash of the building, so one biller's sites
    are different places. Its kind is the kind of center NPPES registers its
    billers as (centers), the first in the order of `kinds` where they're
    registered as two, else `practice`. Billers with different names in
    different suites of a building stay apart, because their prices differ more
    often than one practice's billing IDs do. A doctor's own NPI takes the name of its
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
            WITH RECURSIVE billers AS (
                SELECT b.biller, n.npi, c.kind AS center, k.position AS center_rank,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name ELSE d.org_name END AS name,
                       CASE WHEN n.entity_type = 'organization' THEN n.org_name
                            ELSE coalesce(d.org_name, concat_ws(' ', n.first_name, n.last_name)) END AS biller_name
                FROM (SELECT DISTINCT biller FROM biller_rates
                      UNION SELECT DISTINCT biller FROM facility_rates WHERE center) b
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(b.biller AS BIGINT)
                LEFT JOIN {DOCTORS_CLINICIANS.read(data_dir)} d ON d.npi = n.npi
                LEFT JOIN (SELECT taxonomy_code, kind FROM centers UNION SELECT taxonomy_code, kind FROM facilities) c ON c.taxonomy_code = n.taxonomy_code
                LEFT JOIN kinds k ON k.kind = c.kind
            ),
            listed AS {listed(data_dir)},
            sites AS (
                SELECT DISTINCT ON (b.biller, a.building)
                       b.biller, b.biller_name, b.name, b.center, b.center_rank, l.main, a.building, a.lat, a.lon,
                       concat_ws(', ', l.address_line1, nullif(trim(l.address_line2), '')) AS address,
                       l.city, left(l.postal_code, 5) AS zip, {_phone("l.phone")} AS phone,
                       {suite("l.address_line1", "l.address_line2")} AS suite
                FROM billers b
                JOIN listed l USING (npi)
                JOIN addresses a USING ({ADDRESS})
                ORDER BY b.biller, a.building, NOT l.main, {", ".join(f"l.{c}" for c in ADDRESS.split(", "))}
            ),
            -- Two sites in a building are one practice when their names match or they
            -- share an office (a suite, or no suite); `linked` chains those, so `together`
            -- reaches every site of a practice and its lowest biller labels the group.
            candidates AS (
                SELECT *, {practice_name("name")} AS practice
                FROM sites
                WHERE name IS NOT NULL AND building NOT IN (SELECT building FROM hospitals)
            ),
            linked AS (
                SELECT a.biller AS a, b.biller AS b, a.building
                FROM candidates a JOIN candidates b ON a.building = b.building
                 AND (a.practice = b.practice OR a.suite = b.suite)
            ),
            together(a, b, building) AS (
                SELECT a, b, building FROM linked
                UNION
                SELECT t.a, l.b, t.building FROM together t JOIN linked l ON t.b = l.a AND t.building = l.building
            ),
            groups AS (
                SELECT a AS biller, building, min(b) AS grp FROM together GROUP BY a, building
            ),
            practices AS (
                SELECT biller, biller_name, main, building,
                       coalesce(min(biller) FILTER (WHERE main) OVER practice, min(biller) OVER practice) AS lowest,
                       bool_or(main) OVER practice AS any_main,
                       arg_min(center, center_rank) OVER practice AS center,
                       arg_min(biller, (-coalesce(n.services, 0), biller)) OVER practice AS named
                FROM candidates JOIN groups USING (biller, building)
                LEFT JOIN (SELECT biller, count(DISTINCT service_id) AS services FROM biller_rates GROUP BY biller) n
                    USING (biller)
                WINDOW practice AS (PARTITION BY building, grp)
            )
            SELECT s.biller, s.biller_name, h.ccn AS place_id, 'hospital' AS kind, h.name, h.address, h.city,
                   left(h.postal_code, 5) AS zip, h.lat, h.lon, h.phone, s.main, s.building
            FROM sites s JOIN hospitals h USING (building)
            UNION ALL
            SELECT p.biller, p.biller_name,
                   CASE WHEN p.any_main THEN p.lowest ELSE p.lowest || '-' || left(md5(p.building), 8) END,
                   coalesce(p.center, 'practice'),
                   nm.name, o.address, o.city, o.zip, o.lat, o.lon, o.phone, p.main, p.building
            FROM practices p JOIN sites o ON o.biller = p.lowest AND o.building = p.building
            JOIN sites nm ON nm.biller = p.named AND nm.building = p.building
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
