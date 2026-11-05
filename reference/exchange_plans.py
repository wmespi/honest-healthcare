#!/usr/bin/env python3
"""
Build Georgia's individual-market plan list from the exchange's public use files:

  data/reference/exchange_plans_ga.parquet         one row per plan, keyed on its HIOS plan ID
  data/reference/exchange_networks_ga.parquet      one row per provider network those plans use
  data/reference/exchange_premiums_ga.parquet      monthly premium by plan, rating area and age
  data/reference/exchange_cost_sharing_ga.parquet  what a plan charges the member, by benefit

  Georgia Access public use files: https://georgiaaccess.gov/public-use-files/

Why: Anthem's index names a plan only with an internal label, and Georgia runs
its own exchange, so its plans aren't in CMS's federal files. These files list
every plan anyone can buy on the exchange, keyed on the same HIOS plan IDs the
insurers' indexes use, with its real name, metal level, referral rule, network,
deductible, premiums and cost sharing.

Rows kept: medical plans; dental-only plans are dropped. A plan's cost-sharing
versions (-01 to -06) are one row, named after the standard version (-01),
because they change what the member pays, not the network or the rates; the
deductible and cost sharing kept are the standard version's.

Usage:
  python3 -m reference.exchange_plans [--year 2026]
                                      [--plan-file F --network-file F --rate-file F --benefits-file F]
                                      [--data-dir data] [--test]
"""
import argparse
import datetime
import re
import urllib.parse
import urllib.request

import duckdb

from ._common import fetch_to_cache, ref_dir, write_parquet_atomic

PAGE = "https://georgiaaccess.gov/public-use-files/"
# The site refuses Python's default User-Agent.
HEADERS = {"User-Agent": "honest-healthcare/1.0 (+https://github.com/wmespi/honest-healthcare)"}
# Rows put empty fields unquoted beside quoted ones, which DuckDB's sniffer can't settle alone.
CSV = "all_varchar=true, delim=',', quote='\"', header=true, strict_mode=false, null_padding=true, parallel=false"
# The files read: plans, networks, premiums, and benefits and cost sharing.
KINDS = ("PLAN", "NTWRK", "RATE", "BENCS")


def _latest_urls(year: int) -> dict:
    """The newest PLAN, NTWRK, RATE and BENCS file for `year` linked from the page.
    Georgia republishes a file under a new timestamped name, so the newest
    timestamp wins."""
    with urllib.request.urlopen(urllib.request.Request(PAGE, headers=HEADERS), timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    found = {}
    for kind in KINDS:
        links = re.findall(rf'href="([^"]*/{kind}-PUF_(\d+)_{year}(?:-\d+)?\.csv)"', html)
        if not links:
            raise SystemExit(f"{PAGE}: no {kind} file for {year}")
        found[kind] = urllib.parse.urljoin(PAGE, max(links, key=lambda link: link[1])[0])
    return found


def _source(rd: str, url: str) -> str:
    return fetch_to_cache(f"{rd}/.cache/{url.rsplit('/', 1)[-1]}", [url], headers=HEADERS,
                          header_check=b'"BusinessYear"')


def _check(con, table: str, key: str) -> None:
    dup = con.execute(f"SELECT {key}, count(*) FROM {table} GROUP BY {key} HAVING count(*) > 1 LIMIT 3").fetchall()
    if dup:
        raise SystemExit(f"{table}: {key} isn't unique, for example {dup}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=datetime.date.today().year, help="plan year")
    ap.add_argument("--plan-file", default=None, help="local PLAN-PUF csv (skips the download)")
    ap.add_argument("--network-file", default=None, help="local NTWRK-PUF csv")
    ap.add_argument("--rate-file", default=None, help="local RATE-PUF csv")
    ap.add_argument("--benefits-file", default=None, help="local BENCS-PUF csv")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    rd = ref_dir(args.data_dir, args.test)
    local = (args.plan_file, args.network_file, args.rate_file, args.benefits_file)
    if all(local):
        plan, network, rate, benefits = local
    elif any(local):
        raise SystemExit("pass all four of --plan-file, --network-file, --rate-file and --benefits-file, or none")
    else:
        urls = _latest_urls(args.year)
        plan, network, rate, benefits = (_source(rd, urls[k]) for k in KINDS)
    print(f"→ Georgia Access public use files, plan year {args.year}")

    con = duckdb.connect()
    for path in (plan, network, rate, benefits):
        years = {y for (y,) in con.execute(f"SELECT DISTINCT BusinessYear FROM read_csv('{path}', {CSV})").fetchall()}
        if years != {str(args.year)}:
            raise SystemExit(f"{path}: plan years {sorted(years)}, expected {args.year}")

    con.execute(f"""
        CREATE TABLE plans AS
        SELECT StandardComponentId AS plan_id,
               CAST(BusinessYear AS INTEGER) AS plan_year,
               IssuerId AS issuer_id,
               IssuerMarketPlaceMarketingName AS carrier,
               PlanMarketingName AS name,
               MetalLevel AS metal_level,
               PlanType AS plan_type,
               IsReferralRequiredForSpecialist = 'Yes' AS needs_referral,
               IssuerId || '-' || NetworkId AS network_id,
               CAST(replace(ltrim(CASE MedicalDrugDeductiblesIntegrated
                                       WHEN 'Yes' THEN TEHBDedInnTier1Individual
                                       ELSE MEHBDedInnTier1Individual END, '$'), ',', '') AS DOUBLE) AS deductible
        FROM read_csv('{plan}', {CSV})
        WHERE DentalOnlyPlan = 'No' AND PlanId = StandardComponentId || '-01'
    """)
    # The cost sharing in network, as Georgia words it, such as "$300.00 Copay after
    # deductible"; build/tables/cost_sharing.py reads the wording.
    con.execute(f"""
        CREATE TABLE cost_sharing AS
        SELECT StandardComponentId AS plan_id,
               trim(BenefitName, ' ' || chr(9)) AS benefit,
               IsCovered = 'Covered' AS covered,
               CopayInnTier1 AS copay,
               CoinsInnTier1 AS coinsurance
        FROM read_csv('{benefits}', {CSV})
        WHERE PlanId = StandardComponentId || '-01' AND StandardComponentId IN (SELECT plan_id FROM plans)
    """)
    con.execute(f"""
        CREATE TABLE networks AS
        SELECT IssuerId || '-' || NetworkId AS network_id, IssuerId AS issuer_id, NetworkName AS name
        FROM read_csv('{network}', {CSV})
        WHERE IssuerId || '-' || NetworkId IN (SELECT network_id FROM plans)
    """)
    # "0-14" is one rate for every child, and "64 and over" one for every older adult,
    # so each age gets its own row and a lookup is min(age, 64).
    con.execute(f"""
        CREATE TABLE premiums AS
        WITH r AS (
            SELECT PlanId AS plan_id,
                   CAST(regexp_extract(RatingAreaId, '(\\d+)$', 1) AS INTEGER) AS rating_area,
                   Age AS age_band,
                   CAST(IndividualRate AS DOUBLE) AS premium,
                   CAST(NULLIF(IndividualTobaccoRate, '') AS DOUBLE) AS tobacco_rate
            FROM read_csv('{rate}', {CSV})
            WHERE PlanId IN (SELECT plan_id FROM plans)
        )
        SELECT plan_id, rating_area,
               unnest(CASE age_band WHEN '0-14' THEN range(0, 15)
                                    WHEN '64 and over' THEN [64]
                                    ELSE [CAST(age_band AS BIGINT)] END) AS age,
               premium,
               COALESCE(tobacco_rate, premium) AS tobacco_premium
        FROM r
    """)

    _check(con, "plans", "plan_id")
    _check(con, "networks", "network_id")
    _check(con, "premiums", "plan_id, rating_area, age")
    _check(con, "cost_sharing", "plan_id, benefit")
    orphans = con.execute("SELECT plan_id, network_id FROM plans WHERE network_id NOT IN "
                          "(SELECT network_id FROM networks) LIMIT 3").fetchall()
    if orphans:
        raise SystemExit(f"plans whose network isn't in the network file, for example {orphans}")
    missing = con.execute("SELECT plan_id FROM plans WHERE deductible IS NULL "
                          "OR plan_id NOT IN (SELECT plan_id FROM cost_sharing) ORDER BY plan_id LIMIT 3").fetchall()
    if missing:
        raise SystemExit(f"plans with no in-network deductible or no cost sharing, for example {missing}")

    for table in ("plans", "networks", "premiums", "cost_sharing"):
        out = f"{rd}/exchange_{table}_ga.parquet"
        write_parquet_atomic(con, f"SELECT * FROM {table} ORDER BY ALL", out)
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  wrote {out} ({n:,} rows)")


if __name__ == "__main__":
    main()
