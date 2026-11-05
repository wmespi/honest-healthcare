"""prices.parquet: what a network's contract pays for a service at a place, by
the bills a patient there would get."""
from build.inputs.datasets import NPPES
from build.table import Table


def _part(name, rate):
    return f"{{'part': '{name}', 'max': {rate}}}"


class Prices(Table):
    """One row per network, service and place where the place's billers have a
    rate for the service (build/inputs/biller_rates.py), for the places on the
    map. Each part is one bill, with the highest rate the plan lists for it
    there, since the file doesn't say which of several applies:

    - a hospital's row is its own fee, which Anthem's professional file doesn't
      price, so it's empty, and the reading by the groups in its building. Their
      rates put the hospital on the list, and nothing on file confirms the
      hospital itself is in network, so the screen says so (decided on #135);
    - a place that does the scan shows its all-in rate, which covers the
      reading, or the scan and the reading when it lists no all-in rate;
    - a reading group elsewhere shows the scan, empty because someone else does
      it, and its reading (the 2026-11-04 decision on #135). One with no reading
      rate has no row, since it would show nothing at a place nobody goes to for
      the scan.

    `several_rates` says a part shown has more than one rate on file, so
    someone should ask which they'd get, and `billers` lists the billing NPIs
    behind the row. `providers` lists the clinicians Medicare shows billing the
    service for those billers (build/inputs/biller_doctors.py), never the whole
    roster (#135 step 1), and is empty when Medicare shows none, as at an
    imaging center it has no record of. An organization's own NPI isn't a
    clinician, and neither is a doctor with no Georgia NPPES record as a person."""
    name = "prices"
    key = ["network_id", "service_id", "place_id"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "place_id": "VARCHAR",
               "parts": "STRUCT(part VARCHAR, max DOUBLE)[]", "several_rates": "BOOLEAN",
               "billers": "VARCHAR[]", "providers": "BIGINT[]"}

    def select(self, data_dir):
        empty = "NULL::DOUBLE"
        return f"""
            WITH clinicians AS (
                SELECT d.network_id, d.service_id, b.place_id, list(DISTINCT d.npi ORDER BY d.npi) AS providers
                FROM biller_doctors d
                JOIN biller_places b USING (biller)
                JOIN {NPPES.read(data_dir)} n ON n.npi = d.npi AND n.entity_type = 'individual'
                GROUP BY d.network_id, d.service_id, b.place_id
            ),
            rows AS (
                SELECT r.network_id, r.service_id, b.place_id, any_value(b.kind) = 'hospital' AS hospital,
                       bool_or(r.does_scan) AS does_scan, list(DISTINCT r.biller ORDER BY r.biller) AS billers,
                       {", ".join(f"max(r.rate) FILTER (WHERE r.part = '{p}') AS {p}, "
                                  f"count(DISTINCT r.rate) FILTER (WHERE r.part = '{p}') AS {p}_rates"
                                  for p in ("all_in", "scan", "reading"))}
                FROM biller_rates r JOIN biller_places b USING (biller)
                WHERE b.place_id IN (SELECT place_id FROM places)
                GROUP BY r.network_id, r.service_id, b.place_id
            )
            SELECT network_id, service_id, place_id,
                   CASE WHEN hospital THEN [{_part("facility", empty)}, {_part("reading", "reading")}]
                        WHEN does_scan AND all_in IS NOT NULL THEN [{_part("all_in", "all_in")}]
                        WHEN does_scan THEN [{_part("scan", "scan")}, {_part("reading", "reading")}]
                        ELSE [{_part("scan", empty)}, {_part("reading", "reading")}] END AS parts,
                   CASE WHEN hospital OR NOT does_scan THEN reading_rates > 1
                        WHEN all_in IS NOT NULL THEN all_in_rates > 1
                        ELSE scan_rates > 1 OR reading_rates > 1 END AS several_rates,
                   billers, coalesce(c.providers, []::BIGINT[]) AS providers
            FROM rows
            LEFT JOIN clinicians c USING (network_id, service_id, place_id)
            WHERE hospital OR does_scan OR reading IS NOT NULL
        """

    def check(self, con, data_dir):
        """Every row's network, service and place is in its table."""
        for column, table in [("network_id", "networks"), ("service_id", "services"), ("place_id", "places")]:
            stray = con.execute(f"""
                SELECT {column} FROM prices WHERE {column} NOT IN (SELECT {column} FROM {table}) LIMIT 1
            """).fetchone()
            if stray:
                raise SystemExit(f"prices: {column} {stray[0]} isn't in {table}")
