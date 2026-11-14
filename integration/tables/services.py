"""services.parquet: one row per service someone can find a price for, with what
the screen says about it, its bills and what Medicare pays for it."""
from integration.inputs.datasets import MPFS, OPPS
from integration.inputs.service_list import SERVICE_BILLS
from integration.table import Table


class Services(Table):
    """Every service in integration/services.yaml with a row in prices, with its
    `family` and its text. `bills` is each bill a patient could get for it, in
    its family's order (integration/families.yaml): its `part`, the `label` the
    screen shows, its `billing_class` (`institutional` for the hospital's own fee,
    else `professional`), the `benefits` the member's share for it falls under
    (cost_sharing), and what the screen says where it has no rate (`missing_note`,
    `missing_explain`, and `missing_explain_hospital` where that differs at a
    hospital). `medicare_rate` is what Medicare pays for the whole service in an
    office, the median of Georgia's payment areas (ingestion/reference/mpfs.md).
    `medicare_facility_rate` is Medicare's national hospital outpatient rate
    (ingestion/reference/opps.md), empty for a service Medicare doesn't pay that
    way, such as a mammogram."""
    name = "services"
    key = ["service_id"]
    columns = {"service_id": "VARCHAR", "billing_code": "VARCHAR", "family": "VARCHAR", "name": "VARCHAR",
               "keywords": "VARCHAR", "about": "VARCHAR", "could_change": "VARCHAR[]", "asks": "VARCHAR[]",
               "preventive_note": "VARCHAR",
               "bills": "STRUCT(part VARCHAR, \"label\" VARCHAR, billing_class VARCHAR, benefits VARCHAR[], "
                        "missing_note VARCHAR, missing_explain VARCHAR, missing_explain_hospital VARCHAR)[]",
               "medicare_rate": "DOUBLE", "medicare_facility_rate": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            WITH office AS (
                SELECT billing_code, round(median(medicare_allowed), 2) AS medicare_rate
                FROM {MPFS.read(data_dir)}
                WHERE pos = 'nonfacility' AND coalesce(modifier, '') = '' AND medicare_allowed IS NOT NULL
                GROUP BY billing_code
            ),
            bills AS (
                SELECT service_id,
                       list({{'part': part, 'label': label, 'billing_class': billing_class, 'benefits': benefits,
                              'missing_note': missing_note, 'missing_explain': missing_explain,
                              'missing_explain_hospital': missing_explain_hospital}} ORDER BY position) AS bills
                FROM ({SERVICE_BILLS})
                GROUP BY service_id
            )
            SELECT s.service_id, s.billing_code, s.family, s.name, s.keywords, s.about, s.could_change, s.asks,
                   s.preventive_note, b.bills, o.medicare_rate, f.payment_rate AS medicare_facility_rate
            FROM service_list s
            JOIN bills b USING (service_id)
            LEFT JOIN office o USING (billing_code)
            LEFT JOIN {OPPS.read(data_dir)} f USING (billing_code)
            WHERE s.service_id IN (SELECT service_id FROM prices)
        """
