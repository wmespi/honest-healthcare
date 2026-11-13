"""prices.parquet: what a network's contract pays for a service at a place, by
the bills a patient there would get."""
from integration.inputs.biller_places import practice_name
from integration.inputs.biller_rates import EVIDENCE
from integration.inputs.datasets import NPPES
from integration.table import Table


def _part(name, rate, source=None):
    """SQL for one bill: its rate, and where the rate is from, the plan's own file
    (`plan`) unless `source` says otherwise. Both are empty when nothing prices it."""
    source = source or f"CASE WHEN {rate} IS NOT NULL THEN 'plan' END"
    return f"{{'part': '{name}', 'max': {rate}, 'source': {source}}}"


class Prices(Table):
    """One row per network, service and place on the map where the place's
    billers have a rate for the service (integration/inputs/biller_rates.py) and
    it's one of their places for it (integration/inputs/service_sites.py), plus
    one per network, service and hospital on the map with its own fee for the
    service (integration/inputs/hospital_fees.py). Each part is one bill, with
    the rate that has the strongest evidence there (biller_rates.EVIDENCE), and
    the highest of those when several tie, since the file doesn't say which of
    them applies:

    - a hospital's row is its own fee (`facility`) and the reading by the groups
      in its building. The hospital is on the list where those groups have a
      rate (decided on #135), or where it has a fee, since a fee is evidence the
      hospital itself is in network. The fee is from one source, never a blend
      (hospital_fees): the plan's own file where it prices the hospital, else
      the hospital's own price file under a name the reviewed list says is the
      network's. Only where the fee is empty does nothing on file confirm the
      hospital itself is in network, so the screen says so. An empty reading at a
      hospital means another group reads the scan and bills it separately, and
      the plan's file doesn't place one there, so `billers` and `providers` are
      empty too unless a group there has a rate for another part. `cash_price` is
      what the hospital's own file says it charges for the service when no plan
      pays (hospital_cash), and it's empty at every other place;
    - a place that does the scan shows its all-in rate, which covers the
      reading, or the scan and the reading when it lists no all-in rate;
    - a reading group elsewhere shows the scan, empty because someone else does
      it, and its reading (the 2026-11-04 decision on #135). One with no reading
      rate has no row, since it would show nothing at a place nobody goes to for
      the scan.

    Each part says where its rate is from (`source`): `plan` for the plan's own
    file, `hospital` for a hospital's own fee from its own file, and nothing
    when the part has no rate.

    `several_rates` says a part shown has more than one rate with that evidence,
    or at that source for a hospital's fee, so someone should ask which they'd
    get, and `billers` lists the names of the billing groups behind the row
    (biller_places.biller_name), which at a hospital are the groups reading
    there. Names that match once punctuation and company endings are dropped are
    one group, under its lowest billing NPI's spelling, as a practice is.
    `providers` lists the clinicians Medicare shows billing the service for those
    billers, never the whole roster (#135 step 1): the ones CMS lists practicing
    in the building, or all of them at a biller's main place when it lists none
    at any of its places (service_sites.providers). It's empty when Medicare
    shows none, as at an imaging center it has no record of. An organization's
    own NPI isn't a clinician, and neither is a doctor with no Georgia NPPES
    record as a person."""
    name = "prices"
    key = ["network_id", "service_id", "place_id"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "place_id": "VARCHAR",
               "parts": 'STRUCT(part VARCHAR, max DOUBLE, "source" VARCHAR)[]', "several_rates": "BOOLEAN",
               "billers": "VARCHAR[]", "providers": "BIGINT[]", "cash_price": "DOUBLE"}

    def select(self, data_dir):
        empty = "NULL::DOUBLE"
        rank = f"list_position({list(EVIDENCE)!r}, r.evidence)"
        return f"""
            WITH clinicians AS (
                SELECT d.network_id, d.service_id, d.place_id, list(DISTINCT d.npi ORDER BY d.npi) AS providers
                FROM (SELECT network_id, service_id, place_id, unnest(providers) AS npi FROM service_sites) d
                JOIN {NPPES.read(data_dir)} n ON n.npi = d.npi AND n.entity_type = 'individual'
                GROUP BY d.network_id, d.service_id, d.place_id
            ),
            groups AS (
                SELECT network_id, service_id, place_id, list(name ORDER BY name) AS billers
                FROM (SELECT s.network_id, s.service_id, s.place_id, arg_min(b.biller_name, b.biller) AS name
                      FROM service_sites s JOIN biller_places b USING (biller, place_id)
                      GROUP BY s.network_id, s.service_id, s.place_id, {practice_name("b.biller_name")})
                GROUP BY network_id, service_id, place_id
            ),
            rated AS (
                SELECT r.network_id, r.service_id, b.place_id, b.kind, r.part, r.rate,
                       bool_or(r.does_scan) OVER (PARTITION BY r.network_id, r.service_id, b.place_id) AS does_scan,
                       {rank} = min({rank}) OVER (
                           PARTITION BY r.network_id, r.service_id, b.place_id, r.part) AS strongest
                FROM biller_rates r
                JOIN service_sites s USING (network_id, service_id, biller)
                JOIN biller_places b USING (biller, place_id)
                WHERE b.place_id IN (SELECT place_id FROM places)
            ),
            rows AS (
                SELECT network_id, service_id, place_id, any_value(kind) = 'hospital' AS hospital,
                       bool_or(does_scan) AS does_scan,
                       {", ".join(f"max(rate) FILTER (WHERE part = '{p}') AS {p}, "
                                  f"count(DISTINCT rate) FILTER (WHERE part = '{p}') AS {p}_rates"
                                  for p in ("all_in", "scan", "reading"))}
                FROM rated
                WHERE strongest
                GROUP BY network_id, service_id, place_id
                UNION ALL BY NAME
                SELECT network_id, service_id, place_id, true AS hospital, false AS does_scan, 0 AS reading_rates
                FROM hospital_fees
                WHERE place_id IN (SELECT place_id FROM places)
                  AND (network_id, service_id, place_id) NOT IN (SELECT (network_id, service_id, place_id) FROM rated)
            )
            SELECT network_id, service_id, place_id,
                   CASE WHEN hospital THEN [{_part("facility", "f.fee", "f.source")}, {_part("reading", "reading")}]
                        WHEN does_scan AND all_in IS NOT NULL THEN [{_part("all_in", "all_in")}]
                        WHEN does_scan THEN [{_part("scan", "scan")}, {_part("reading", "reading")}]
                        ELSE [{_part("scan", empty)}, {_part("reading", "reading")}] END AS parts,
                   CASE WHEN hospital THEN reading_rates > 1 OR coalesce(f.rates, 0) > 1
                        WHEN NOT does_scan THEN reading_rates > 1
                        WHEN all_in IS NOT NULL THEN all_in_rates > 1
                        ELSE scan_rates > 1 OR reading_rates > 1 END AS several_rates,
                   coalesce(g.billers, []::VARCHAR[]) AS billers, coalesce(c.providers, []::BIGINT[]) AS providers,
                   CASE WHEN hospital THEN h.cash_price END AS cash_price
            FROM rows
            LEFT JOIN groups g USING (network_id, service_id, place_id)
            LEFT JOIN clinicians c USING (network_id, service_id, place_id)
            LEFT JOIN hospital_fees f USING (network_id, service_id, place_id)
            LEFT JOIN hospital_cash h USING (service_id, place_id)
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
