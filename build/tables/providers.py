"""providers.parquet: the clinicians behind the prices, so someone with a
referral can find a place by a provider's name."""
from build.inputs.datasets import MIPS, NPPES, NUCC_TAXONOMY
from build.table import Table


class Providers(Table):
    """One row per clinician and network: someone Medicare shows billing a
    service for a place's biller (build/inputs/biller_doctors.py), never the
    whole roster (#135 step 1). `place_ids` lists the places on that network with
    a price for a service they bill. A row is per network because rosters differ
    by network, so one list of networks and one of places would show a provider
    at a place on a network where they aren't behind its price.

    The name and specialty come from the provider's own NPPES record, the
    specialty as NUCC's name for its primary code, and `score` is Medicare's
    quality (MIPS) score, empty when it has none. A billing doctor with no
    Georgia NPPES record as a person isn't listed."""
    name = "providers"
    key = ["npi", "network_id"]
    columns = {"npi": "BIGINT", "network_id": "VARCHAR", "name": "VARCHAR", "specialty": "VARCHAR",
               "score": "DOUBLE", "place_ids": "VARCHAR[]"}

    def select(self, data_dir):
        return f"""
            WITH listed AS (
                SELECT DISTINCT d.npi, p.network_id, p.place_id
                FROM biller_doctors d
                JOIN biller_places b USING (biller)
                JOIN prices p
                  ON p.network_id = d.network_id AND p.service_id = d.service_id AND p.place_id = b.place_id
            )
            SELECT l.npi, l.network_id, concat_ws(' ', n.first_name, n.last_name) AS name,
                   t.display_name AS specialty, m.mips_score AS score,
                   list(l.place_id ORDER BY l.place_id) AS place_ids
            FROM listed l
            JOIN {NPPES.read(data_dir)} n ON n.npi = l.npi AND n.entity_type = 'individual'
            LEFT JOIN {NUCC_TAXONOMY.read(data_dir)} t ON t.taxonomy_code = n.taxonomy_code
            LEFT JOIN {MIPS.read(data_dir)} m ON m.npi = l.npi
            GROUP BY l.npi, l.network_id, n.first_name, n.last_name, t.display_name, m.mips_score
        """

    def check(self, con, data_dir):
        """Every provider has a name and a specialty, and every network and place
        is in its table."""
        blank = con.execute("""
            SELECT npi FROM providers WHERE nullif(trim(name), '') IS NULL OR specialty IS NULL LIMIT 1
        """).fetchone()
        if blank:
            raise SystemExit(f"providers: {blank[0]} has no name or no specialty in NPPES and NUCC")
        for column, table, values in [("network_id", "networks", "network_id"),
                                      ("place_id", "places", "unnest(place_ids)")]:
            stray = con.execute(f"""
                SELECT v FROM (SELECT {values} AS v FROM providers)
                WHERE v NOT IN (SELECT {column} FROM {table}) LIMIT 1
            """).fetchone()
            if stray:
                raise SystemExit(f"providers: {column} {stray[0]} isn't in {table}")
