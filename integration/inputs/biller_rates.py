"""Build-only: each rate a network's file lists for a service, by the biller it
covers, with the evidence that the biller does or reads that service."""
from integration.inputs.datasets import NPPES, PAYER_RATE_GROUPS, PAYER_RATES, PAYER_ROSTERS
from integration.table import Table

# The bill a rate is for, by its modifier: none is the whole service, TC the scan
# and 26 the reading.
PARTS = {"": "all_in", "TC": "scan", "26": "reading"}

# Modifiers that make a rate a different bill from the service's own, so a rate
# with one is left out, as is a rate that names two parts at once. A rate with a
# modifier in neither list stops the build (BillerRates.check), so a new one is
# read before it is priced or dropped.
OTHER_BILLS = {
    "50": "both sides in one visit",
    "62": "two surgeons",
    "80": "an assistant surgeon",
    "AS": "a physician assistant or nurse assisting at surgery",
    "RR": "rented equipment",
    "NU": "new equipment",
    "UE": "used equipment",
    "QW": "a lab test a clinic may run itself",
}

# Why a rate is believed to be one a patient would get, strongest first. prices
# shows a place's rate with the strongest evidence, so the order is a product rule.
#   medicare_roster   Medicare shows a doctor on the rate's own roster billing the service
#   medicare_biller   Medicare shows one behind the biller, though not on this rate's roster
#   registered        the biller is registered as a center for that kind of service
EVIDENCE = ("medicare_roster", "medicare_biller", "registered")

# The rates the build reads: a fee-for-service dollar amount for an outpatient
# professional bill.
PRICED = """r.billing_class = 'professional' AND r.setting IN ('outpatient', 'both')
              AND r.negotiation_arrangement = 'ffs'
              AND r.negotiated_type IN ('fee schedule', 'negotiated')"""


def covered(data_dir):
    """SQL for the CTEs that end in `covered`: each kept rate with the biller and
    each doctor (`npi`) on the rosters it covers. biller_doctors reads it too."""
    modifiers = ", ".join(f"'{m}'" for m in PARTS)
    part = " ".join(f"WHEN '{m}' THEN '{p}'" for m, p in PARTS.items())
    return f"""
        lines AS (
            SELECT r.network_id, s.service_id, r.group_set_id,
                   CASE coalesce(r.modifier, '') {part} END AS part, r.negotiated_rate AS rate,
                   coalesce(r.service_code, '') IN ('', 'CSTM-00')
                       OR contains('|' || r.service_code || '|', '|11|') AS office_line
            FROM {PAYER_RATES.read(data_dir)} r
            JOIN service_list s ON s.billing_code = r.billing_code
            WHERE {PRICED}
              AND coalesce(r.modifier, '') IN ({modifiers})
        ),
        kept AS (
            SELECT * FROM lines l
            WHERE office_line OR NOT EXISTS (
                SELECT 1 FROM lines o
                WHERE o.network_id = l.network_id AND o.service_id = l.service_id AND o.office_line)
        ),
        medians AS (
            SELECT network_id, service_id, part, median(rate) AS median FROM kept
            GROUP BY network_id, service_id, part
        ),
        covered AS (
            SELECT DISTINCT k.network_id, k.service_id, k.part, k.rate, p.tin_value AS biller, p.npi
            FROM kept k
            JOIN medians m USING (network_id, service_id, part)
            JOIN {PAYER_RATE_GROUPS.read(data_dir)} g USING (network_id, group_set_id)
            JOIN {PAYER_ROSTERS.read(data_dir)} p USING (network_id, provider_group_id)
            WHERE k.rate > greatest(1, 0.05 * m.median)
        )"""


class BillerRates(Table):
    """Rates are the outpatient professional ones: fee-for-service, a fee schedule
    or negotiated dollar amount, for an outpatient visit. A service with a line
    that applies in an office keeps only those lines, and a placeholder rate, at
    most $1 or 5% of the median for that service and part on the network, is
    dropped.

    A rate's roster is the provider groups its group set lists, on the rate's
    network.

    A biller appears for a service if Medicare shows a doctor billing it for that
    biller (biller_doctors), or if its NPPES primary specialty code registers it
    as an imaging center that does that kind of scan (imaging_centers), because
    Medicare's file often misses those centers. `does_scan` says it's such a
    center, or one of its doctors billed the service in an office. Otherwise the
    biller only reads the scan (the 2026-11-04 decisions on #135).

    `evidence` is the strongest of EVIDENCE behind the rate. A biller often sits
    under several fee schedules, each with its own roster, and the file doesn't
    say which a patient gets, so a rate whose own roster has a doctor Medicare
    shows billing the service outranks one whose roster has none (#191)."""
    name = "biller_rates"
    key = ["network_id", "service_id", "biller", "part", "rate"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR",
               "part": "VARCHAR", "rate": "DOUBLE", "does_scan": "BOOLEAN", "evidence": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH {covered(data_dir)},
            on_roster AS (
                SELECT DISTINCT c.network_id, c.service_id, c.biller, c.part, c.rate
                FROM covered c
                JOIN biller_doctors d USING (network_id, service_id, biller, npi)
            ),
            behind AS (
                SELECT network_id, service_id, biller, bool_or(in_office) AS in_office
                FROM biller_doctors
                GROUP BY network_id, service_id, biller
            ),
            registered AS (
                SELECT DISTINCT c.network_id, c.service_id, c.biller
                FROM covered c
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(c.biller AS BIGINT)
                JOIN imaging_centers i ON i.taxonomy_code = n.taxonomy_code
                WHERE i.scans IS NULL OR i.scans = split_part(c.service_id, '-', 1)
            )
            SELECT DISTINCT c.network_id, c.service_id, c.biller, c.part, c.rate,
                   r.biller IS NOT NULL OR coalesce(e.in_office, false) AS does_scan,
                   CASE WHEN o.biller IS NOT NULL THEN 'medicare_roster'
                        WHEN e.biller IS NOT NULL THEN 'medicare_biller'
                        ELSE 'registered' END AS evidence
            FROM covered c
            LEFT JOIN on_roster o USING (network_id, service_id, biller, part, rate)
            LEFT JOIN behind e USING (network_id, service_id, biller)
            LEFT JOIN registered r USING (network_id, service_id, biller)
            WHERE e.biller IS NOT NULL OR r.biller IS NOT NULL
        """

    def check(self, con, data_dir):
        """Every modifier on a service's rates is one the build has a rule for.

        A biller is found by its NPI, which is what Anthem lists as every billing
        ID. Rates that list a tax ID (EIN) instead stop the build here, rather
        than every biller in them landing on the no-NPPES-record list."""
        known = ", ".join(f"'{m}'" for m in (*PARTS, *OTHER_BILLS))
        unknown = [m for (m,) in con.execute(f"""
            SELECT DISTINCT modifier
            FROM (SELECT unnest(string_split(coalesce(r.modifier, ''), '|')) AS modifier
                  FROM {PAYER_RATES.read(data_dir)} r
                  JOIN service_list s ON s.billing_code = r.billing_code
                  WHERE {PRICED})
            WHERE modifier NOT IN ({known})
            ORDER BY modifier
        """).fetchall()]
        if unknown:
            raise SystemExit(f"biller_rates: rates for a listed service carry modifiers {unknown}, which the build "
                             f"has no rule for; add each to PARTS or OTHER_BILLS in integration/inputs/biller_rates.py")
        other = con.execute(f"""
            SELECT tin_type, count(*) FROM {PAYER_ROSTERS.read(data_dir)}
            WHERE tin_type IS DISTINCT FROM 'npi'
            GROUP BY tin_type ORDER BY 2 DESC
        """).fetchone()
        if other:
            raise SystemExit(f"biller_rates: {other[1]:,} roster rows bill under a {other[0]!r} ID, not an NPI, "
                             f"so NPPES can't say where they are")
