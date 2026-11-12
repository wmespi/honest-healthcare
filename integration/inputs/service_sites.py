"""Build-only: the places where each biller's rate for a service is shown."""
from integration.inputs.addresses import ADDRESS
from integration.inputs.datasets import CLINICIAN_LOCATIONS
from integration.table import Table


class ServiceSites(Table):
    """One row per network, service, biller and place of the biller's
    (integration/inputs/biller_places.py) where someone could get the service
    from it:

    - a place in a building CMS lists one of the biller's doctors for the
      service practicing in (integration/inputs/biller_doctors.py), with those
      doctors as its `providers`;
    - every place of a biller NPPES registers as a center for the service
      (biller_rates.registered), since a center does its scans wherever it is;
    - the biller's main place, with every doctor it has for the service, when
      CMS lists none of them in any of its buildings. Nothing says which of its
      places does the service, and its own record's address is the one it gave.

    `providers` is empty where CMS lists none of the doctors, as at most centers'
    other places. A service a biller has doctors for at one place isn't shown at
    its others, so a group's billing office drops out once its doctors are seen
    at the clinic."""
    name = "service_sites"
    key = ["network_id", "service_id", "biller", "place_id"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR", "place_id": "VARCHAR",
               "providers": "BIGINT[]"}

    def select(self, data_dir):
        return f"""
            WITH practicing AS (
                SELECT DISTINCT c.npi, a.building
                FROM (SELECT npi, address_line1, coalesce(address_line2, '') AS address_line2, city, state,
                             postal_code
                      FROM {CLINICIAN_LOCATIONS.read(data_dir)}) c
                JOIN addresses a USING ({ADDRESS})
            ),
            seen AS (
                SELECT d.network_id, d.service_id, d.biller, s.place_id, list(DISTINCT d.npi ORDER BY d.npi) AS providers
                FROM biller_doctors d
                JOIN biller_places s USING (biller)
                JOIN practicing p ON p.npi = d.npi AND p.building = s.building
                GROUP BY d.network_id, d.service_id, d.biller, s.place_id
            ),
            everyone AS (
                SELECT network_id, service_id, biller, list(DISTINCT npi ORDER BY npi) AS providers
                FROM biller_doctors
                GROUP BY network_id, service_id, biller
            ),
            pairs AS (
                SELECT r.network_id, r.service_id, r.biller, bool_or(r.registered) AS registered,
                       (r.network_id, r.service_id, r.biller) IN (SELECT (network_id, service_id, biller) FROM seen)
                           AS any_seen
                FROM biller_rates r
                GROUP BY r.network_id, r.service_id, r.biller
            )
            SELECT p.network_id, p.service_id, p.biller, s.place_id,
                   coalesce(n.providers, CASE WHEN s.main AND NOT p.any_seen THEN e.providers END, []::BIGINT[])
                       AS providers
            FROM pairs p
            JOIN biller_places s USING (biller)
            LEFT JOIN seen n USING (network_id, service_id, biller, place_id)
            LEFT JOIN everyone e USING (network_id, service_id, biller)
            WHERE n.place_id IS NOT NULL OR p.registered OR (s.main AND NOT p.any_seen)
        """
