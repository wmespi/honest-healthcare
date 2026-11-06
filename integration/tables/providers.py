"""providers.parquet: who each clinician a price lists is, so someone with a
referral can find a place by a provider's name."""
from integration.inputs.datasets import MIPS, NPPES, NUCC_TAXONOMY
from integration.table import Table


class Providers(Table):
    """One row per clinician in a `prices` row's `providers`. The name and
    specialty come from the clinician's own NPPES record, the specialty as
    NUCC's name for its primary code, and `score` is Medicare's quality (MIPS)
    score, empty when it has none."""
    name = "providers"
    key = ["npi"]
    columns = {"npi": "BIGINT", "name": "VARCHAR", "specialty": "VARCHAR", "score": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT n.npi, concat_ws(' ', n.first_name, n.last_name) AS name, t.display_name AS specialty,
                   m.mips_score AS score
            FROM (SELECT DISTINCT unnest(providers) AS npi FROM prices) p
            JOIN {NPPES.read(data_dir)} n ON n.npi = p.npi
            LEFT JOIN {NUCC_TAXONOMY.read(data_dir)} t ON t.taxonomy_code = n.taxonomy_code
            LEFT JOIN {MIPS.read(data_dir)} m ON m.npi = n.npi
        """

    def check(self, con, data_dir):
        """Every provider has a name and a specialty, and every clinician a price
        lists is here."""
        blank = con.execute("""
            SELECT npi FROM providers WHERE nullif(trim(name), '') IS NULL OR specialty IS NULL LIMIT 1
        """).fetchone()
        if blank:
            raise SystemExit(f"providers: {blank[0]} has no name or no specialty in NPPES and NUCC")
        stray = con.execute("""
            SELECT npi FROM (SELECT unnest(providers) AS npi FROM prices)
            WHERE npi NOT IN (SELECT npi FROM providers) LIMIT 1
        """).fetchone()
        if stray:
            raise SystemExit(f"providers: prices lists {stray[0]}, who isn't in providers")
