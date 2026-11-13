"""`make coverage`: what the build keeps at each step, from the rates a
network's file lists to the rows the screen shows (integration/build.md).

Makes the same tables `make build` does, from <data-dir>/ingestion/, and writes
nothing; data-test/ stands in for <data-dir> under --test.
"""
import argparse
import os
import shutil
import tempfile

from integration import tables
from integration.inputs.biller_rates import EVIDENCE, covered
from integration.inputs.hospital_plans import HOSPITAL_PLANS_PATH
from integration.inputs.service_list import SERVICES_PATH

# Each step of the funnel, in order, and what it counts. A pair is a service and
# a biller on a network; a row is a service at a place.
STEPS = {
    "priced": "service and biller pairs with a rate",
    "evidenced": "pairs with evidence the biller does the service",
    **{kind: f"  strongest evidence {kind}" for kind in EVIDENCE},
    "placed": "pairs with a place for the service",
    "on_map": "pairs with one of those places on the map",
    "shown": "rows shown",
    "several_rates": "rows with more than one rate to ask about",
    "with_clinician": "rows naming a clinician",
    "places": "places shown",
    "at_hospital": "rows at a hospital",
    "fee_and_reading": "  showing its own fee and a reading",
    "fee_only": "  showing its own fee and no reading",
    "reading_only": "  showing a reading and no fee of its own",
    "neither": "  showing neither, since its groups list no reading rate",
    "fee_plan": "  with its own fee from the plan's file",
    "fee_hospital": "  with its own fee from the hospital's file",
    "cash_price": "  with a cash price",
}


def _part(name):
    """SQL for a prices row's part `name`."""
    return f"list_filter(p.parts, x -> x.part = '{name}')[1]"


def report(con, data_dir, by_service=False):
    """The funnel as one dict per network, or per network and service, keyed by
    STEPS. Needs the build's tables made in `con` (integration.tables.make)."""
    keys = "network_id, service_id" if by_service else "network_id"
    kinds = ", ".join(f"count(*) FILTER (WHERE strongest = {i}) AS {kind}" for i, kind in enumerate(EVIDENCE, 1))
    cur = con.execute(f"""
        WITH {covered(data_dir)},
        pairs AS (
            SELECT c.network_id, c.service_id, c.biller, count(s.place_id) > 0 AS placed,
                   count(p.place_id) > 0 AS on_map,
                   min(list_position({list(EVIDENCE)!r}, r.evidence)) AS strongest
            FROM (SELECT DISTINCT network_id, service_id, biller FROM covered) c
            LEFT JOIN biller_rates r USING (network_id, service_id, biller)
            LEFT JOIN service_sites s USING (network_id, service_id, biller)
            LEFT JOIN places p USING (place_id)
            GROUP BY c.network_id, c.service_id, c.biller
        ),
        funnel AS (
            SELECT {keys}, count(*) AS priced, count(strongest) AS evidenced, {kinds},
                   count(*) FILTER (WHERE placed) AS placed, count(*) FILTER (WHERE on_map) AS on_map
            FROM pairs
            GROUP BY {keys}
        ),
        shown AS (
            SELECT {keys}, count(*) AS shown, count(*) FILTER (WHERE several_rates) AS several_rates,
                   count(*) FILTER (WHERE len(providers) > 0) AS with_clinician,
                   count(DISTINCT place_id) AS places
            FROM prices
            GROUP BY {keys}
        ),
        hospital_rows AS (
            SELECT p.network_id, p.service_id, {_part("facility")}.max IS NOT NULL AS fee,
                   {_part("reading")}.max IS NOT NULL AS reading, {_part("facility")}.source AS source, p.cash_price
            FROM prices p JOIN places l USING (place_id)
            WHERE l.kind = 'hospital'
        ),
        at_hospitals AS (
            SELECT {keys}, count(*) AS at_hospital,
                   count(*) FILTER (WHERE fee AND reading) AS fee_and_reading,
                   count(*) FILTER (WHERE fee AND NOT reading) AS fee_only,
                   count(*) FILTER (WHERE reading AND NOT fee) AS reading_only,
                   count(*) FILTER (WHERE NOT fee AND NOT reading) AS neither,
                   count(*) FILTER (WHERE source = 'plan') AS fee_plan,
                   count(*) FILTER (WHERE source = 'hospital') AS fee_hospital, count(cash_price) AS cash_price
            FROM hospital_rows
            GROUP BY {keys}
        )
        SELECT {keys}, {", ".join(f"coalesce({step}, 0) AS {step}" for step in STEPS)}
        FROM funnel FULL JOIN shown USING ({keys}) FULL JOIN at_hospitals USING ({keys})
        ORDER BY {keys}
    """)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def show(rows, by_service=False):
    """Print the funnel: a list of steps per network, or one line per service."""
    if not by_service:
        for row in rows:
            print(f"\n{row['network_id']}")
            for step, label in STEPS.items():
                print(f"  {row[step]:>9,}  {label}")
        return
    steps = ["priced", "evidenced", "on_map", "shown", "several_rates"]
    width = max(len(row["service_id"]) for row in rows)
    network = None
    for row in rows:
        if row["network_id"] != network:
            network = row["network_id"]
            print(f"\n{network}\n  {'':<{width}}" + "".join(f"{step:>15}" for step in steps))
        print(f"  {row['service_id']:<{width}}" + "".join(f"{row[step]:>15,}" for step in steps))


def coverage(data_dir, by_service=False, services_path=SERVICES_PATH, hospital_plans_path=HOSPITAL_PLANS_PATH):
    """Make the build's tables from `data_dir` in a connection of its own and
    return the funnel's rows."""
    spill = os.getenv("DUCKDB_TMP") or tempfile.mkdtemp(prefix="coverage-")
    con = tables.connect(spill)
    try:
        tables.make(con, data_dir, services_path, hospital_plans_path)
        return report(con, data_dir, by_service)
    finally:
        con.close()
        if not os.getenv("DUCKDB_TMP"):
            shutil.rmtree(spill, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.getenv("DATA_DIR", "/app/data"))
    ap.add_argument("--test", action="store_true", help="read under data-test/")
    ap.add_argument("--by", choices=["network", "service"], default="network",
                    help="one list per network (the default), or one line per service")
    args = ap.parse_args()

    root = "data-test" if args.test else args.data_dir
    by_service = args.by == "service"
    show(coverage(f"{root}/ingestion", by_service), by_service)


if __name__ == "__main__":
    main()
