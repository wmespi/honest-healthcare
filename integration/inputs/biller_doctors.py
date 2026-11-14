"""Build-only: the doctors Medicare shows billing a service, by the biller whose
rate for it covers them."""
from integration.inputs.biller_rates import covered
from integration.inputs.datasets import MEDICARE_BILLING
from integration.table import Table


class BillerDoctors(Table):
    """A doctor is behind a biller's rate when they're on a roster it covers
    (biller_rates.covered), or are the biller, billing under their own NPI, and
    Medicare shows them billing the service: its own code, or any of its family's
    `evidence_codes` where the family names them, and never where it names none
    (integration/families.yaml). `in_office` says Medicare shows them billing it
    in an office. biller_rates reads this as its evidence, and providers as who
    is at a place."""
    name = "biller_doctors"
    key = ["network_id", "service_id", "biller", "npi"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR", "npi": "BIGINT",
               "in_office": "BOOLEAN"}

    def select(self, data_dir):
        return f"""
            WITH {covered(data_dir)},
            evidence AS (
                SELECT s.service_id, unnest(coalesce(f.evidence_codes, [s.billing_code])) AS code
                FROM service_list s JOIN family_list f USING (family)
            ),
            billed AS (
                SELECT m.npi, e.service_id, bool_or(m.place_of_service = 'O') AS in_office
                FROM {MEDICARE_BILLING.read(data_dir)} m
                JOIN evidence e ON e.code = m.hcpcs_cd
                GROUP BY m.npi, e.service_id
            )
            SELECT c.network_id, c.service_id, c.biller, c.npi, b.in_office
            FROM (SELECT network_id, service_id, biller, npi FROM covered
                  UNION SELECT network_id, service_id, biller, TRY_CAST(biller AS BIGINT) FROM covered) c
            JOIN billed b ON b.npi = c.npi AND b.service_id = c.service_id
        """
