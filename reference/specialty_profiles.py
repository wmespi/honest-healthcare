#!/usr/bin/env python3
"""
Build data/reference/specialty_procedure_profiles.parquet — for each provider
specialty, the procedures that a meaningful share of that specialty actually
performs, learned from CMS Medicare utilization.

This is Tier 2 of the provider↔procedure story (issue #14). Tier 1 (`did_bill`)
answers "did *this NPI* bill this code" — strong, but only ~43% of the providers
we can price have any Medicare footprint, and it misses commercial-only work.
Tier 2 generalises: if the code is billed by, say, ≥3% of the provider's
specialty, it's plausible for them even without a direct utilization row. It
covers ~92% of priceable providers.

How: join CMS by-provider-and-service → NPPES (by NPI) → NUCC taxonomy, so every
billed (NPI, HCPCS) is tagged with the provider's NUCC *classification*
("Cardiology", "Pediatrics", …). Then per classification: prevalence =
(distinct NPIs billing the code) / (distinct NPIs in the classification).

Pure relational reshape over landed Parquet — Python/DuckDB per
../AGENTS.md#the-language-principle. Depends on `make cms-utilization`,
`make nppes`, and `make taxonomy-labels` having run.

Output columns (data/reference/specialty_procedure_profiles.parquet):
  specialty            NUCC classification
  hcpcs_cd
  billers              distinct NPIs of this specialty that billed the code
  specialty_providers  distinct NPIs of this specialty with any Medicare claim
  prevalence           billers / specialty_providers  (0..1)

All codes with prevalence ≥ 0.005 are kept; the serving layer thresholds at
query time (default 0.03). Specialties with < 20 Medicare providers are dropped
(too small to profile).

Usage:
  python3 -m reference.specialty_profiles [--data-dir data] [--test]
"""
import argparse
import sys

import duckdb

from ._common import ref_dir, write_parquet_atomic

PREVALENCE_FLOOR = 0.005
MIN_SPECIALTY_PROVIDERS = 20


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    rd = ref_dir(args.data_dir, args.test)
    cms = f"{data_dir}/cms/ga_provider_service.parquet"
    nppes = f"{data_dir}/nppes/ga_providers.parquet"
    nucc = f"{rd}/nucc_taxonomy.parquet"
    out_path = f"{rd}/specialty_procedure_profiles.parquet"

    con = duckdb.connect()
    for label, path in [("cms-utilization", cms), ("nppes", nppes), ("taxonomy-labels", nucc)]:
        try:
            con.execute(f"SELECT 1 FROM read_parquet('{path}') LIMIT 1")
        except duckdb.IOException:
            print(f"  missing {path} — run `make {label}` first", file=sys.stderr)
            sys.exit(1)

    con.execute(f"""
        CREATE TABLE billed AS
        SELECT COALESCE(x.classification, 'Unknown') AS specialty,
               u.npi, u.hcpcs_cd AS hcpcs_cd
        FROM read_parquet('{cms}') u
        JOIN read_parquet('{nppes}') n ON n.npi = u.npi
        LEFT JOIN read_parquet('{nucc}') x ON x.taxonomy_code = n.taxonomy_code
        GROUP BY 1, 2, 3
    """)

    write_parquet_atomic(
        con,
        f"""
        WITH spec_n AS (
            SELECT specialty, count(DISTINCT npi) AS specialty_providers
            FROM billed GROUP BY 1
            HAVING count(DISTINCT npi) >= {MIN_SPECIALTY_PROVIDERS}
        ),
        code_n AS (
            SELECT specialty, hcpcs_cd, count(DISTINCT npi) AS billers
            FROM billed GROUP BY 1, 2
        )
        SELECT c.specialty, c.hcpcs_cd, c.billers, n.specialty_providers,
               round(c.billers::DOUBLE / n.specialty_providers, 4) AS prevalence
        FROM code_n c JOIN spec_n n USING (specialty)
        WHERE c.billers::DOUBLE / n.specialty_providers >= {PREVALENCE_FLOOR}
          AND c.specialty <> 'Unknown'
        """,
        out_path,
    )

    rows, specs = con.execute(f"""
        SELECT count(*), count(DISTINCT specialty) FROM read_parquet('{out_path}')
    """).fetchone()
    print(f"→ wrote {out_path}")
    print(f"  {rows:,} (specialty, code) rules across {specs} specialties")
    for t in (0.01, 0.03, 0.05, 0.10):
        n = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}') WHERE prevalence >= {t}").fetchone()[0]
        print(f"    prevalence ≥ {t:>4}: {n:,} rules")
    big = con.execute(f"""
        SELECT specialty, count(*) n FROM read_parquet('{out_path}')
        WHERE prevalence >= 0.03 GROUP BY 1 ORDER BY n DESC LIMIT 10
    """).fetchall()
    for s, n in big:
        print(f"    {n:>4}  {s}")


if __name__ == "__main__":
    main()
