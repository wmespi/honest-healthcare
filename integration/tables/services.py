"""services.parquet: one row per service someone can find a price for, with what
the screen says about it, its bills and what Medicare pays for it."""
from integration.inputs.biller_rates import OWN_FEE_FAMILIES
from integration.inputs.datasets import MPFS, OPPS
from integration.inputs.service_list import SERVICE_BILLS, SERVICE_EXTRA_BILLS
from integration.table import Table


class Services(Table):
    """Every service in integration/services.yaml with a row in prices, with its
    `family` and its text. `bills` is each bill a patient could get for it, in
    its family's order (integration/families.yaml): its `part`, the `label` the
    screen shows, its `billing_class` (`institutional` for a facility's own fee,
    else `professional`), the `benefits` the member's share for it falls under
    (cost_sharing), and what the screen says where it has no rate (`missing_note`,
    `missing_explain`, `missing_explain_hospital` where that differs at a
    hospital, and `missing_note_practice` with `missing_explain_practice` at a practice). `medicare_rate` is what Medicare pays for the whole service in an
    office, empty for a family whose facility bills its own fee, since Medicare's
    physician rate covers only the doctor's part, the median of Georgia's payment areas (ingestion/reference/mpfs.md).
    `extra_bills` is the bills it brings that the patient doesn't choose who
    bills, anesthesia and a lab's exam of removed tissue, each with its `part`,
    `label`, `note`, the `missing` note for a plan with no rate, and the `code`
    (extra_bills prices them). `medicare_facility_rate` is Medicare's national hospital outpatient rate
    (ingestion/reference/opps.md), empty for a service Medicare doesn't pay that
    way, such as a mammogram."""
    name = "services"
    key = ["service_id"]
    columns = {"service_id": "VARCHAR", "billing_code": "VARCHAR", "family": "VARCHAR", "name": "VARCHAR",
               "keywords": "VARCHAR", "about": "VARCHAR", "could_change": "VARCHAR[]", "asks": "VARCHAR[]",
               "preventive_note": "VARCHAR",
               "bills": "STRUCT(part VARCHAR, \"label\" VARCHAR, billing_class VARCHAR, benefits VARCHAR[], "
                        "missing_note VARCHAR, missing_explain VARCHAR, missing_explain_hospital VARCHAR, "
                        "missing_note_practice VARCHAR, missing_explain_practice VARCHAR)[]",
               "extra_bills": "STRUCT(part VARCHAR, \"label\" VARCHAR, note VARCHAR, missing VARCHAR, "
                              "code VARCHAR)[]",
               "medicare_rate": "DOUBLE", "medicare_facility_rate": "DOUBLE"}

    def select(self, data_dir):
        return f"""
            WITH office AS (
                SELECT billing_code, billing_code_type, round(median(medicare_allowed), 2) AS medicare_rate
                FROM {MPFS.read(data_dir)}
                WHERE pos = 'nonfacility' AND coalesce(modifier, '') = '' AND medicare_allowed IS NOT NULL
                GROUP BY billing_code, billing_code_type
            ),
            bills AS (
                SELECT service_id,
                       list({{'part': part, 'label': label, 'billing_class': billing_class, 'benefits': benefits,
                              'missing_note': missing_note, 'missing_explain': missing_explain,
                              'missing_explain_hospital': missing_explain_hospital,
                              'missing_note_practice': missing_note_practice,
                              'missing_explain_practice': missing_explain_practice}} ORDER BY position) AS bills
                FROM ({SERVICE_BILLS})
                GROUP BY service_id
            ),
            extras AS (
                SELECT service_id,
                       list({{'part': part, 'label': label, 'note': note, 'missing': missing, 'code': code}}
                            ORDER BY part) AS extra_bills
                FROM ({SERVICE_EXTRA_BILLS})
                GROUP BY service_id
            )
            SELECT s.service_id, s.billing_code, s.family, s.name, s.keywords, s.about, s.could_change, s.asks,
                   s.preventive_note, b.bills,
                   coalesce(e.extra_bills, []::STRUCT(part VARCHAR, "label" VARCHAR, note VARCHAR, missing VARCHAR, code VARCHAR)[]) AS extra_bills,
                   CASE WHEN s.family NOT IN {OWN_FEE_FAMILIES} THEN o.medicare_rate END AS medicare_rate,
                   f.payment_rate AS medicare_facility_rate
            FROM service_list s
            JOIN bills b USING (service_id)
            LEFT JOIN extras e USING (service_id)
            LEFT JOIN office o ON o.billing_code = s.billing_code AND list_contains(s.code_types, o.billing_code_type)
            LEFT JOIN {OPPS.read(data_dir)} f
              ON f.billing_code = s.billing_code AND list_contains(s.code_types, f.billing_code_type)
            WHERE s.service_id IN (SELECT service_id FROM prices)
        """
