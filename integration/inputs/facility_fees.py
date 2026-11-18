"""Build-only: a facility's own fee for a service on a network: a hospital's, from
one source, the plan's own file else the hospital's own price file, and a center's,
from the plan's own file."""
from integration.inputs.addresses import ADDRESS, listed
from integration.inputs.datasets import HOSPITAL_RATES, NPPES, PAYER_RATE_GROUPS, PAYER_RATES, PAYER_ROSTERS, own_first, roster_biller
from integration.inputs.hospital_plans import PLAN_NETWORKS, plan_name
from integration.table import Table

# A dollar amount in a hospital's own file at or above this is a placeholder, not a
# price: files write runs of nines, such as 99999 or 999999999, where they have none.
CEILING = 99_999

# The institutional rates the build reads from a plan's file: a fee-for-service
# (`ffs`) dollar amount (`fee schedule` or `negotiated`) for the service's CPT or
# HCPCS code with no modifier, on an outpatient bill or one for either setting
# (`both`) or none, above $1. Left out: a percentage of charges, a per diem, a
# `derived` amount, an inpatient bill and a bundle, none of which is the fee for
# one outpatient visit.
FACILITY_PRICED = """r.billing_class = 'institutional' AND r.billing_code_type IN ('CPT', 'HCPCS')
              AND coalesce(r.setting, 'both') IN ('outpatient', 'both') AND r.negotiation_arrangement = 'ffs'
              AND r.negotiated_type IN ('fee schedule', 'negotiated') AND coalesce(r.modifier, '') = ''
              AND r.negotiated_rate > 1"""


# The institutional rates the build reads from a plan's file for a hospital stay
# (an MS-DRG): a fee-for-service dollar amount for the DRG with no modifier, above
# $1, a `negotiated` or `fee schedule` amount being the stay's price and a `per
# diem` a day's. Its setting isn't read: an MS-DRG pays only for a stay, and a
# file can call it outpatient while naming the inpatient hospital as its place of
# service (21). Left out: a `derived` amount, which is the payer's own estimate
# (CMS's spec), and a bundle.
STAY_PRICED = """r.billing_class = 'institutional' AND r.billing_code_type = 'MS-DRG'
              AND r.negotiation_arrangement = 'ffs'
              AND r.negotiated_type IN ('fee schedule', 'negotiated', 'per diem') AND coalesce(r.modifier, '') = ''
              AND r.negotiated_rate > 1"""


# A stay's dollar amount that one biller (or hospital) lists for more than this many
# distinct MS-DRGs is a default, not the stay's price: tiers of related DRGs stay under it.
MAX_TIERED_DRGS = 25

# The services a hospital bills its own fee for: those whose family has an
# institutional bill (integration/families.yaml).
FEE_SERVICES = """(SELECT service_id, billing_code, code_types FROM service_list
                   WHERE family IN (SELECT family FROM family_bills WHERE billing_class = 'institutional'))"""

# The services a center bills its own fee for: those whose family offers the
# institutional bill away from a hospital, in one of its `elsewhere` choices.
CENTER_FEE_SERVICES = """(SELECT s.service_id FROM service_list s JOIN family_list f USING (family)
                          JOIN family_bills b ON b.family = f.family AND b.billing_class = 'institutional'
                          WHERE list_contains(flatten(f.elsewhere), b.part))"""


def facility_lines(r):
    """SQL for the rows of a hospital's own file `r` that price a service's
    facility bill: its CPT or HCPCS code, in an outpatient visit or a setting the
    row doesn't name, on a bill that isn't only the doctor's, with no modifier."""
    return (f"{r}.code_type IN ('CPT', 'HCPCS') AND coalesce({r}.setting, 'both') IN ('outpatient', 'both') "
            f"AND coalesce({r}.billing_class, 'both') != 'professional' AND coalesce({r}.modifiers, '') = ''")


def stay_lines(r):
    """SQL for the rows of a hospital's own file `r` that price a stay: its MS-DRG
    code, on a bill that isn't only the doctor's, with no modifier."""
    return (f"{r}.code_type = 'MS-DRG' AND coalesce({r}.billing_class, 'both') != 'professional' "
            f"AND coalesce({r}.modifiers, '') = ''")


def fee_schedule(r):
    """SQL for the rows of a hospital's own file `r` whose dollar amount is the
    plan's fee for the service: its `fee schedule` rows, as FACILITY_PRICED keeps
    the plan file's fee schedule and negotiated dollar lines. A percentage row's
    dollar is the hospital's estimate from its own charge, a per diem is a day's
    rate, a case rate covers more than the service, and a row with no
    methodology doesn't say which it is, so none of them is the fee. The cash
    price is the hospital's, not a contract's, so hospital_cash doesn't use this."""
    return f"{r}.methodology = 'fee schedule'"


def priced(amount):
    """SQL for a dollar amount in a hospital's own file that is a price: above $1
    and below CEILING."""
    return f"{amount} > 1 AND {amount} < {CEILING}"


class FacilityRates(Table):
    """One row per network, service a facility bills its own fee for
    (FEE_SERVICES), billing NPI and institutional rate the network's file lists
    for the service (FACILITY_PRICED, or STAY_PRICED for a stay, where `per_day`
    says the rate is a day's), whose code type is the service's (service_list.code_types), covering a roster with that billing NPI. A
    rate under the file's shared labels is kept only where the network's own
    labels have none for the biller and service (datasets.own_first). A billing
    NPI counts only where NPPES registers it as a hospital organization, or as a
    center of the service's family (centers), or as a facility (`generic`, whose
    fee counts only where service_sites shows a group with evidence at the place), where the family offers the fee away
    from a hospital (CENTER_FEE_SERVICES), which `center` says, since a
    facility's own fee is what's priced."""
    name = "facility_rates"
    key = ["network_id", "service_id", "biller", "rate", "per_day"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR", "rate": "DOUBLE",
               "per_day": "BOOLEAN", "center": "BOOLEAN", "generic": "BOOLEAN"}

    def select(self, data_dir):
        return f"""
            SELECT DISTINCT network_id, service_id, biller, rate, per_day, center, generic FROM (
                SELECT DISTINCT r.network_id, s.service_id, {roster_biller('p')} AS biller, r.negotiated_rate AS rate,
                       r.negotiated_type = 'per diem' AS per_day, r.own_label, NOT n.is_hospital AS center,
                       n.taxonomy_code IN (SELECT taxonomy_code FROM facilities) AS generic
                FROM {PAYER_RATES.read(data_dir)} r
                JOIN {FEE_SERVICES} s ON s.billing_code = r.billing_code AND list_contains(s.code_types, r.billing_code_type)
                JOIN {PAYER_RATE_GROUPS.read(data_dir)} g USING (network_id, group_set_id)
                JOIN {PAYER_ROSTERS.read(data_dir)} p USING (network_id, provider_group_id)
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST({roster_biller('p')} AS BIGINT)
                WHERE ({FACILITY_PRICED} OR ({STAY_PRICED}
                        AND (r.negotiated_type = 'per diem'
                             OR (r.network_id, {roster_biller('p')}, r.negotiated_rate) NOT IN
                                (SELECT (r2.network_id, {roster_biller('p2')}, r2.negotiated_rate)
                                 FROM {PAYER_RATES.read(data_dir)} r2
                                 JOIN {PAYER_RATE_GROUPS.read(data_dir)} g2 USING (network_id, group_set_id)
                                 JOIN {PAYER_ROSTERS.read(data_dir)} p2 USING (network_id, provider_group_id)
                                 WHERE {STAY_PRICED.replace("r.", "r2.")} AND r2.negotiated_type != 'per diem'
                                 GROUP BY ALL HAVING count(DISTINCT r2.billing_code) > {MAX_TIERED_DRGS}))))
                  AND n.entity_type = 'organization'
                  AND (n.is_hospital
                       OR (s.service_id IN {CENTER_FEE_SERVICES}
                           AND (n.taxonomy_code IN (SELECT taxonomy_code FROM facilities)
                                OR EXISTS (SELECT 1 FROM service_list v JOIN centers t USING (family)
                                           WHERE v.service_id = s.service_id AND t.taxonomy_code = n.taxonomy_code)))))
            QUALIFY {own_first("network_id", "service_id", "biller", "per_day")}
        """


class FacilityFees(Table):
    """One row per network, service and place with a price for the place's own
    fee, from one source, never a blend of two. A place is a hospital (`place_id`,
    its CCN) or a center a service's family registers, the practice
    (integration/inputs/biller_places.py) of a billing NPI NPPES registers as one:

    - `plan`: the network's own file, wherever a rate in facility_rates covers a
      hospital billing NPI with an NPPES address, its own or another it lists, in
      the hospital's building (integration/inputs/addresses.py), or a center's
      billing NPI, at each place it has;
    - `hospital`: only where the plan's file prices none there, the hospital's
      own price file, on its fee schedule rows (fee_schedule) for the service's
      code (facility_lines) under a name integration/hospital_plans.yaml says
      means the network, with a negotiated dollar amount that is a price
      (priced), which a center has no counterpart of.

    `fee` is the highest rate at that source and `rates` how many different ones
    there are, since neither file says which applies.

    A hospital stay (an MS-DRG service, in a family with the institutional bill
    and no `elsewhere`) is priced the same way from one source, plan file first,
    in one of three terms, the first the source has: a dollar `fee` (the plan's
    negotiated or fee schedule amount; the hospital's fee schedule or case rate),
    else `per_day` (a day's rate: a per diem), else `percent`, of the hospital's
    charges (the hospital's own file only, whose dollar amount for it is an
    estimate). `fee` is empty on a row priced in another term, and `rates` counts
    the rates of the term shown."""
    name = "facility_fees"
    key = ["network_id", "service_id", "place_id"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "place_id": "VARCHAR", "fee": "DOUBLE",
               "per_day": "DOUBLE", "percent": "DOUBLE", "rates": "BIGINT", "source": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH plan_rates AS (
                SELECT f.network_id, f.service_id, h.ccn AS place_id,
                       max(f.rate) FILTER (WHERE NOT f.per_day) AS fee, max(f.rate) FILTER (WHERE f.per_day) AS per_day,
                       count(DISTINCT f.rate) FILTER (WHERE NOT f.per_day) AS rates,
                       count(DISTINCT f.rate) FILTER (WHERE f.per_day) AS per_day_rates
                FROM facility_rates f
                JOIN {listed(data_dir)} l ON l.npi = TRY_CAST(f.biller AS BIGINT)
                JOIN addresses a USING ({ADDRESS})
                JOIN hospitals h USING (building)
                WHERE NOT f.center
                GROUP BY f.network_id, f.service_id, h.ccn
                UNION ALL
                SELECT f.network_id, f.service_id, b.place_id, max(f.rate), NULL, count(DISTINCT f.rate), 0
                FROM facility_rates f
                JOIN biller_places b ON b.biller = f.biller AND b.kind != 'hospital'
                WHERE f.center AND (NOT f.generic OR (f.network_id, f.service_id, b.place_id) IN (
                    SELECT (network_id, service_id, place_id) FROM service_sites))
                GROUP BY f.network_id, f.service_id, b.place_id
            ),
            plan AS (
                SELECT network_id, service_id, place_id, fee, CASE WHEN fee IS NULL THEN per_day END AS per_day,
                       NULL::DOUBLE AS percent, CASE WHEN fee IS NULL THEN per_day_rates ELSE rates END AS rates
                FROM plan_rates
            ),
            own AS (
                SELECT p.network_id, s.service_id, r.ccn AS place_id, max(r.negotiated_dollar) AS fee,
                       NULL::DOUBLE AS per_day, NULL::DOUBLE AS percent, count(DISTINCT r.negotiated_dollar) AS rates
                FROM {HOSPITAL_RATES.read(data_dir)} r
                JOIN {FEE_SERVICES} s ON s.billing_code = r.code AND list_contains(s.code_types, r.code_type)
                JOIN {PLAN_NETWORKS} p ON p.ccn = r.ccn AND p.name = {plan_name("r")}
                WHERE {facility_lines("r")} AND {fee_schedule("r")}
                  AND {priced("r.negotiated_dollar")} AND r.ccn IN (SELECT ccn FROM hospitals)
                GROUP BY p.network_id, s.service_id, r.ccn
            ),
            own_stays AS (
                SELECT p.network_id, s.service_id, r.ccn AS place_id,
                       max(r.negotiated_dollar) FILTER (WHERE r.methodology IN ('fee schedule', 'case rate')
                                                        AND {priced("r.negotiated_dollar")}) AS fee,
                       max(r.negotiated_dollar) FILTER (WHERE r.methodology = 'per diem'
                                                        AND {priced("r.negotiated_dollar")}) AS per_day,
                       max(r.negotiated_percentage) FILTER (
                           WHERE r.methodology = 'percent of total billed charges'
                             AND r.negotiated_percentage > 0 AND r.negotiated_percentage <= 100) AS percent,
                       count(DISTINCT r.negotiated_dollar) FILTER (WHERE r.methodology IN ('fee schedule', 'case rate')
                                                                   AND {priced("r.negotiated_dollar")}) AS rates,
                       count(DISTINCT r.negotiated_dollar) FILTER (WHERE r.methodology = 'per diem'
                                                                   AND {priced("r.negotiated_dollar")}) AS per_day_rates
                FROM {HOSPITAL_RATES.read(data_dir)} r
                JOIN {FEE_SERVICES} s ON s.billing_code = r.code AND list_contains(s.code_types, r.code_type)
                JOIN {PLAN_NETWORKS} p ON p.ccn = r.ccn AND p.name = {plan_name("r")}
                WHERE {stay_lines("r")} AND r.ccn IN (SELECT ccn FROM hospitals)
                  AND (r.methodology NOT IN ('fee schedule', 'case rate') OR NOT {priced("r.negotiated_dollar")}
                       OR (r.ccn, {plan_name("r")}, r.negotiated_dollar) NOT IN (
                           SELECT (d.ccn, {plan_name("d")}, d.negotiated_dollar)
                           FROM {HOSPITAL_RATES.read(data_dir)} d
                           WHERE {stay_lines("d")} AND d.methodology IN ('fee schedule', 'case rate')
                             AND {priced("d.negotiated_dollar")}
                           GROUP BY ALL HAVING count(DISTINCT d.code) > {MAX_TIERED_DRGS}))
                GROUP BY p.network_id, s.service_id, r.ccn
            ),
            own_stay AS (
                SELECT network_id, service_id, place_id, fee,
                       CASE WHEN fee IS NULL THEN per_day END AS per_day,
                       CASE WHEN fee IS NULL AND per_day IS NULL THEN percent END AS percent,
                       CASE WHEN fee IS NOT NULL THEN rates WHEN per_day IS NOT NULL THEN per_day_rates ELSE 1 END
                           AS rates
                FROM own_stays
                WHERE fee IS NOT NULL OR per_day IS NOT NULL OR percent IS NOT NULL
            )
            SELECT network_id, service_id, place_id, fee, per_day, percent, rates, 'plan' AS source FROM plan
            WHERE fee IS NOT NULL OR per_day IS NOT NULL
            UNION ALL
            SELECT network_id, service_id, place_id, fee, per_day, percent, rates, 'hospital'
            FROM (SELECT * FROM own UNION ALL SELECT * FROM own_stay)
            WHERE (network_id, service_id, place_id) NOT IN (SELECT (network_id, service_id, place_id) FROM plan)
        """

    def check(self, con, data_dir):
        """Not a stop: list every hospital billing NPI with an institutional rate
        that has no address in a Georgia hospital's building, so its rates price
        no hospital's fee. A center's billing NPI isn't listed."""
        ids = [r[0] for r in con.execute(f"""
            SELECT DISTINCT f.biller FROM facility_rates f
            WHERE NOT f.center AND NOT EXISTS (SELECT 1 FROM {listed(data_dir)} l
                              JOIN addresses a USING ({ADDRESS}) JOIN hospitals h USING (building)
                              WHERE l.npi = TRY_CAST(f.biller AS BIGINT))
            ORDER BY f.biller
        """).fetchall()]
        if ids:
            print(f"  hospital billing NPIs with an institutional rate but no address in a hospital's building, "
                  f"so they price no hospital's fee ({len(ids)}): {', '.join(ids)}")
