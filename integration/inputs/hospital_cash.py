"""Build-only: what a hospital's own price file says it charges for a service when
no plan pays."""
from integration.inputs.datasets import HOSPITAL_RATES
from integration.inputs.hospital_fees import FEE_SERVICES, facility_lines, priced
from integration.table import Table


class HospitalCash(Table):
    """One row per service a hospital bills its own fee for
    (hospital_fees.FEE_SERVICES) and hospital (`place_id`, its CCN) whose own price
    file gives a cash price for the service's facility bill: the highest on any
    of its rows for the service's code (hospital_fees.facility_lines) that is a
    price (hospital_fees.priced), whatever the row's payer, since the cash price
    is the hospital's, not a plan's."""
    name = "hospital_cash"
    key = ["service_id", "place_id"]
    columns = {"service_id": "VARCHAR", "place_id": "VARCHAR", "cash_price": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            SELECT s.service_id, r.ccn AS place_id, max(r.cash_price) AS cash_price
            FROM {HOSPITAL_RATES.read(data_dir)} r
            JOIN {FEE_SERVICES} s ON s.billing_code = r.code
            WHERE {facility_lines("r")} AND {priced("r.cash_price")}
              AND r.ccn IN (SELECT ccn FROM hospitals)
            GROUP BY s.service_id, r.ccn
        """
