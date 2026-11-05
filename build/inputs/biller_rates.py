"""Build-only: each rate a network's file lists for a service, by the biller it
covers, for the billers Medicare shows doing or reading that service."""
from build.inputs.datasets import NPPES, PAYER_RATE_GROUPS, PAYER_RATES, PAYER_ROSTERS
from build.table import Table

# The bill part each modifier prices: none is the whole service, TC the scan and
# 26 the reading. Other modifiers, such as 50 for both sides, aren't a part.
PARTS = {"": "all_in", "TC": "scan", "26": "reading"}


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
            WHERE r.billing_class = 'professional' AND r.setting IN ('outpatient', 'both')
              AND r.negotiation_arrangement = 'ffs'
              AND r.negotiated_type IN ('fee schedule', 'negotiated')
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
    biller only reads the scan (the 2026-11-04 decisions on #135)."""
    name = "biller_rates"
    key = ["network_id", "service_id", "biller", "part", "rate"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR",
               "part": "VARCHAR", "rate": "DOUBLE", "does_scan": "BOOLEAN"}

    def select(self, data_dir):
        return f"""
            WITH {covered(data_dir)},
            evidence AS (
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
                   r.biller IS NOT NULL OR coalesce(e.in_office, false) AS does_scan
            FROM covered c
            LEFT JOIN evidence e USING (network_id, service_id, biller)
            LEFT JOIN registered r USING (network_id, service_id, biller)
            WHERE e.biller IS NOT NULL OR r.biller IS NOT NULL
        """

    def check(self, con, data_dir):
        """A biller is found by its NPI, which is what Anthem lists as every billing
        ID. Rates that list a tax ID (EIN) instead stop the build here, rather
        than every biller in them landing on the no-NPPES-record list."""
        other = con.execute(f"""
            SELECT tin_type, count(*) FROM {PAYER_ROSTERS.read(data_dir)}
            WHERE tin_type IS DISTINCT FROM 'npi'
            GROUP BY tin_type ORDER BY 2 DESC
        """).fetchone()
        if other:
            raise SystemExit(f"biller_rates: {other[1]:,} roster rows bill under a {other[0]!r} ID, not an NPI, "
                             f"so NPPES can't say where they are")
