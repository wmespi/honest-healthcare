"""services.parquet: one row per service someone can search for, with what the
screen says about it and what Medicare pays for it."""
from build.inputs.datasets import MPFS, OPPS
from build.table import Table


class Services(Table):
    """The text comes from build/services.yaml. `medicare_rate` is what Medicare
    pays for the whole service in an office, the median of Georgia's payment
    areas (reference/mpfs.md). `medicare_facility_rate` is Medicare's national
    hospital outpatient rate (reference/opps.md), empty for a service Medicare
    doesn't pay that way, such as a mammogram."""
    name = "services"
    key = ["service_id"]
    columns = {"service_id": "VARCHAR", "billing_code": "VARCHAR", "name": "VARCHAR",
               "keywords": "VARCHAR", "about": "VARCHAR", "could_change": "VARCHAR[]",
               "asks": "VARCHAR[]", "preventive_note": "VARCHAR",
               "medicare_rate": "DOUBLE", "medicare_facility_rate": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            WITH office AS (
                SELECT billing_code, round(median(medicare_allowed), 2) AS medicare_rate
                FROM {MPFS.read(data_dir)}
                WHERE pos = 'nonfacility' AND coalesce(modifier, '') = '' AND medicare_allowed IS NOT NULL
                GROUP BY billing_code
            )
            SELECT s.service_id, s.billing_code, s.name, s.keywords, s.about, s.could_change,
                   s.asks, s.preventive_note, o.medicare_rate,
                   f.payment_rate AS medicare_facility_rate
            FROM service_list s
            LEFT JOIN office o USING (billing_code)
            LEFT JOIN {OPPS.read(data_dir)} f USING (billing_code)
        """
