"""Build-only: a hospital's own fee for a service on a network, from one source:
the plan's own file, else the hospital's own price file."""
from integration.inputs.addresses import ADDRESS, listed
from integration.inputs.datasets import HOSPITAL_RATES, NPPES, PAYER_RATE_GROUPS, PAYER_RATES, PAYER_ROSTERS, own_first
from integration.inputs.hospital_plans import plan_name
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


# The services a hospital bills its own fee for: those whose family has an
# institutional bill (integration/families.yaml).
FEE_SERVICES = """(SELECT service_id, billing_code FROM service_list
                   WHERE family IN (SELECT family FROM family_bills WHERE billing_class = 'institutional'))"""


def facility_lines(r):
    """SQL for the rows of a hospital's own file `r` that price a service's
    facility bill: its CPT or HCPCS code, in an outpatient visit or a setting the
    row doesn't name, on a bill that isn't only the doctor's, with no modifier."""
    return (f"{r}.code_type IN ('CPT', 'HCPCS') AND coalesce({r}.setting, 'both') IN ('outpatient', 'both') "
            f"AND coalesce({r}.billing_class, 'both') != 'professional' AND coalesce({r}.modifiers, '') = ''")


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
    """One row per network, service a hospital bills its own fee for
    (FEE_SERVICES), hospital billing NPI and institutional rate the network's
    file lists for the service (FACILITY_PRICED), covering a
    roster with that billing NPI. A rate under the file's shared labels is kept
    only where the network's own labels have none for the hospital and service
    (datasets.own_first). A billing NPI counts only where NPPES registers it as
    a hospital organization, since a hospital's own fee is what's priced."""
    name = "facility_rates"
    key = ["network_id", "service_id", "biller", "rate"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR", "rate": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT DISTINCT network_id, service_id, biller, rate FROM (
                SELECT DISTINCT r.network_id, s.service_id, p.tin_value AS biller, r.negotiated_rate AS rate,
                       r.own_label
                FROM {PAYER_RATES.read(data_dir)} r
                JOIN {FEE_SERVICES} s ON s.billing_code = r.billing_code
                JOIN {PAYER_RATE_GROUPS.read(data_dir)} g USING (network_id, group_set_id)
                JOIN {PAYER_ROSTERS.read(data_dir)} p USING (network_id, provider_group_id)
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(p.tin_value AS BIGINT)
                WHERE {FACILITY_PRICED} AND n.entity_type = 'organization' AND n.is_hospital)
            QUALIFY {own_first("network_id", "service_id", "biller")}
        """


class HospitalFees(Table):
    """One row per network, service and hospital (`place_id`, its CCN) with a
    price for the hospital's own fee, from one source, never a blend of two:

    - `plan`: the network's own file, wherever a rate in facility_rates covers a
      hospital billing NPI with an NPPES address, its own or another it lists, in
      the hospital's building (integration/inputs/addresses.py);
    - `hospital`: only where the plan's file prices none there, the hospital's
      own price file, on its fee schedule rows (fee_schedule) for the service's
      code (facility_lines) under a name integration/hospital_plans.yaml says
      means the network, with a negotiated dollar amount that is a price
      (priced).

    `fee` is the highest rate at that source and `rates` how many different ones
    there are, since neither file says which applies."""
    name = "hospital_fees"
    key = ["network_id", "service_id", "place_id"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "place_id": "VARCHAR", "fee": "DOUBLE",
               "rates": "BIGINT", "source": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH plan AS (
                SELECT f.network_id, f.service_id, h.ccn AS place_id, max(f.rate) AS fee,
                       count(DISTINCT f.rate) AS rates
                FROM facility_rates f
                JOIN {listed(data_dir)} l ON l.npi = TRY_CAST(f.biller AS BIGINT)
                JOIN addresses a USING ({ADDRESS})
                JOIN hospitals h USING (building)
                GROUP BY f.network_id, f.service_id, h.ccn
            ),
            own AS (
                SELECT p.network_id, s.service_id, r.ccn AS place_id, max(r.negotiated_dollar) AS fee,
                       count(DISTINCT r.negotiated_dollar) AS rates
                FROM {HOSPITAL_RATES.read(data_dir)} r
                JOIN {FEE_SERVICES} s ON s.billing_code = r.code
                JOIN hospital_plans p ON p.ccn = r.ccn AND p.name = {plan_name("r")}
                WHERE p.network_id IS NOT NULL AND {facility_lines("r")} AND {fee_schedule("r")}
                  AND {priced("r.negotiated_dollar")} AND r.ccn IN (SELECT ccn FROM hospitals)
                GROUP BY p.network_id, s.service_id, r.ccn
            )
            SELECT network_id, service_id, place_id, fee, rates, 'plan' AS source FROM plan
            UNION ALL
            SELECT network_id, service_id, place_id, fee, rates, 'hospital' FROM own
            WHERE (network_id, service_id, place_id) NOT IN (SELECT (network_id, service_id, place_id) FROM plan)
        """

    def check(self, con, data_dir):
        """Not a stop: list every hospital billing NPI with an institutional rate
        that has no address in a Georgia hospital's building, so its rates price
        no hospital's fee."""
        ids = [r[0] for r in con.execute(f"""
            SELECT DISTINCT f.biller FROM facility_rates f
            WHERE NOT EXISTS (SELECT 1 FROM {listed(data_dir)} l
                              JOIN addresses a USING ({ADDRESS}) JOIN hospitals h USING (building)
                              WHERE l.npi = TRY_CAST(f.biller AS BIGINT))
            ORDER BY f.biller
        """).fetchall()]
        if ids:
            print(f"  hospital billing NPIs with an institutional rate but no address in a hospital's building, "
                  f"so they price no hospital's fee ({len(ids)}): {', '.join(ids)}")
