"""extra_bills.parquet: what a network's plan usually pays for the bills a service
brings that the patient doesn't choose who bills, anesthesia, a lab's exam of
removed tissue and the doctor's fee for a hospital stay."""
from integration.inputs.datasets import PAYER_RATES
from integration.table import Table


# Each part's code a service names, `part` and `code` by `service_id`.
NAMED = """(SELECT service_id, 'anesthesia' AS part, anesthesia AS code FROM service_list
            UNION ALL SELECT service_id, 'pathology', pathology FROM service_list
            UNION ALL SELECT service_id, 'doctor', doctor FROM service_list)"""


class ExtraBills(Table):
    """One row per network, service in `services` and part where the service names
    a code for it (integration/services.yaml) and the network's file lists dollar
    amounts for it. `low`, `typical` and `high` are the 10th percentile, median and
    90th percentile of the network's fee-for-service, fee schedule or negotiated
    professional dollar lines for the code with no modifier, and `lines` how many
    there are.

    Both plans' files give these codes' rates to nearly every provider as a generic
    fee schedule, so who bills them can't be known: the range is what the plan
    usually pays, not a place's. A line of at most $1 or 5% of the network's median
    for the code is a placeholder, often the code's unit count, and is dropped, as in
    biller_rates, and so is a line with any modifier, which prices a different bill
    (a percentage or 1.5 times for both sides), except that anesthesia also reads
    AA (an anesthesiologist personally) and QZ (a nurse anesthetist alone), the
    whole fee where a file lists none without a modifier; QK, QX and QY are a
    supervised share of it. Anesthesia and doctor lines are kept
    whatever setting and place of service they name, because Anthem lists the fee
    schedule under the hospital (place of service 21), and a stay's doctor bills
    there; a lab's exam keeps only outpatient lines, as in biller_rates. `doctor` is
    the CPT code of the doctor's whole fee for a stay (delivery, joint replacement),
    billed apart from the hospital's."""
    name = "extra_bills"
    key = ["network_id", "service_id", "part"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "part": "VARCHAR", "code": "VARCHAR",
               "low": "DOUBLE", "typical": "DOUBLE", "high": "DOUBLE", "lines": "INTEGER"}

    def select(self, data_dir):
        return f"""
            WITH wanted AS (
                SELECT DISTINCT part, code FROM {NAMED} WHERE code IS NOT NULL
            ),
            lines AS (
                SELECT r.network_id, w.part, w.code, r.negotiated_rate AS rate
                FROM {PAYER_RATES.read(data_dir)} r
                JOIN wanted w ON w.code = r.billing_code
                WHERE r.billing_code_type IN ('CPT', 'HCPCS') AND r.billing_class = 'professional' AND r.negotiation_arrangement = 'ffs'
                  AND r.negotiated_type IN ('fee schedule', 'negotiated')
                  AND (coalesce(r.modifier, '') = '' OR (w.part = 'anesthesia' AND r.modifier IN ('AA', 'QZ')))
                  AND r.negotiated_rate IS NOT NULL
                  AND (w.part IN ('anesthesia', 'doctor') OR r.setting IN ('outpatient', 'both'))
            ),
            kept AS (
                SELECT * FROM lines
                QUALIFY rate > greatest(1, 0.05 * median(rate) OVER (PARTITION BY network_id, part, code))
            ),
            spread AS (
                SELECT network_id, part, code,
                       round(quantile_cont(rate, 0.1), 2) AS low, round(median(rate), 2) AS typical,
                       round(quantile_cont(rate, 0.9), 2) AS high, count(*)::INTEGER AS lines
                FROM kept GROUP BY network_id, part, code
            )
            SELECT x.network_id, s.service_id, x.part, x.code, x.low, x.typical, x.high, x.lines
            FROM spread x
            JOIN {NAMED} s USING (part, code)
            WHERE s.service_id IN (SELECT service_id FROM services)
        """
