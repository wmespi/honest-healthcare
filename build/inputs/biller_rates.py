"""Build-only: each rate a network's file lists for a service, by the biller it
covers, for the billers Medicare shows doing or reading that service."""
from build.inputs.datasets import GROUP_SETS, INSURER_RATES, MEDICARE_BILLING, NPPES, ROSTERS
from build.table import Table

# The bill part each modifier prices: none is the whole service, TC the scan and
# 26 the reading. Other modifiers, such as 50 for both sides, aren't a part.
PARTS = {"": "all_in", "TC": "scan", "26": "reading"}


class BillerRates(Table):
    """Rates are the outpatient professional ones: fee-for-service, a fee schedule
    or negotiated dollar amount, for an outpatient visit. A service with a line
    that applies in an office keeps only those lines, and a placeholder rate, at
    most $1 or 5% of the median for that service and part on the network, is
    dropped.

    A rate's roster is the provider groups its group set lists. A group's own
    network label can be several networks joined by `|`, so the price row, not the
    roster, says which network the rate is for.

    A biller appears for a service only if a doctor on its roster, or the biller
    itself, billed Medicare for it. `does_scan` says one of them billed it in an
    office, or the biller is registered as an imaging center (imaging_centers),
    because Medicare's file often misses those centers. Otherwise the biller
    only reads the scan (the 2026-11-04 decision on #135)."""
    name = "biller_rates"
    key = ["network_id", "service_id", "biller", "part", "rate"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR",
               "part": "VARCHAR", "rate": "DOUBLE", "does_scan": "BOOLEAN"}

    def select(self, data_dir):
        modifiers = ", ".join(f"'{m}'" for m in PARTS)
        part = " ".join(f"WHEN '{m}' THEN '{p}'" for m, p in PARTS.items())
        return f"""
            WITH lines AS (
                SELECT f.network_id, s.service_id, r.file_id, r.network_name, r.group_set_id,
                       CASE coalesce(r.modifier, '') {part} END AS part, r.negotiated_rate AS rate,
                       coalesce(r.service_code, '') IN ('', 'CSTM-00')
                           OR contains('|' || r.service_code || '|', '|11|') AS office_line
                FROM {INSURER_RATES.read(data_dir)} r
                JOIN network_files f ON f.file_id = r.file_id AND f.network_name = r.network_name
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
                JOIN {GROUP_SETS.read(data_dir)} g ON g.file_id = k.file_id AND g.group_set_id = k.group_set_id
                JOIN {ROSTERS.read(data_dir)} p
                  ON p.file_id = g.file_id AND p.provider_group_id = g.provider_group_id
                WHERE k.rate > greatest(1, 0.05 * m.median)
            ),
            billed AS (
                SELECT m.npi, s.service_id, bool_or(m.place_of_service = 'O') AS in_office
                FROM {MEDICARE_BILLING.read(data_dir)} m
                JOIN service_list s ON s.billing_code = m.hcpcs_cd
                GROUP BY m.npi, s.service_id
            ),
            evidence AS (
                SELECT c.network_id, c.service_id, c.biller, bool_or(b.in_office) AS in_office
                FROM (SELECT network_id, service_id, biller, npi FROM covered
                      UNION SELECT network_id, service_id, biller, TRY_CAST(biller AS BIGINT) FROM covered) c
                JOIN billed b ON b.npi = c.npi AND b.service_id = c.service_id
                GROUP BY c.network_id, c.service_id, c.biller
            )
            SELECT DISTINCT c.network_id, c.service_id, c.biller, c.part, c.rate,
                   e.in_office OR coalesce(n.taxonomy_code IN (SELECT taxonomy_code FROM imaging_centers),
                                           false) AS does_scan
            FROM covered c
            JOIN evidence e USING (network_id, service_id, biller)
            LEFT JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(c.biller AS BIGINT)
        """
