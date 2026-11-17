"""prices.parquet: what a network's contract pays for a service at a place, by
the bills a patient there would get."""
from integration.inputs.biller_places import practice_name
from integration.inputs.biller_rates import EVIDENCE
from integration.inputs.datasets import NPPES, NUCC_TAXONOMY
from integration.table import Table


class Prices(Table):
    """One row per network, service and place on the map where the place's
    billers have a rate for the service (integration/inputs/biller_rates.py) and
    it's one of their places for it (integration/inputs/service_sites.py), plus
    one per network, service and place on the map with its own fee for the
    service, a hospital's or a center's (integration/inputs/facility_fees.py). Which bills a row shows is the
    service's family's (integration/families.yaml), and each is one part, with
    the rate that has the strongest evidence there (biller_rates.EVIDENCE), and
    the highest of those when several tie, since the file doesn't say which of
    them applies:

    - a hospital's row shows the family's `hospital` bills, in order: its own
      fee (the institutional bill) and the others by the groups in its building,
      such as a scan's reading. The hospital is on the list where those groups
      have a rate (decided on #135), or where it has a fee, since a fee is
      evidence the hospital itself is in network. The fee is from one source,
      never a blend (facility_fees): the plan's own file where it prices the
      hospital, else the hospital's own price file under a name the reviewed
      list says is the network's. Only where the fee is empty does nothing on
      file confirm the hospital itself is in network, so the screen says so. An
      empty reading at a hospital means another group reads the scan and bills
      it separately, and the plan's file doesn't place one there, so `billers`
      and `providers` are empty too unless a group there has a rate for another
      part. `cash_price` is what the hospital's own file says it charges for the
      service when no plan pays (hospital_cash), and it's empty at every other
      place. A family with no `hospital` bills isn't shown at hospitals;
    - any other place shows the first of the family's `elsewhere` alternatives
      it has a rate for every bill of, else the last. A center, such as a surgery
      center, shows its own fee where the plan's file has one, even with no
      professional rate, and the doctor's fee empty. An `on_site` bill counts
      only where the place does the service (biller_rates.on_site) and is
      empty elsewhere. For a scan, a place that does it shows its all-in rate,
      which covers the reading, or the scan and the reading when it lists no
      all-in rate, and a reading group elsewhere shows the scan, empty because
      someone else does it, and its reading (the 2026-11-04 decision on #135).

    A row shows at least one rate, so a reading group with no reading rate has
    none, since it would show nothing at a place nobody goes to for the scan.

    Each part says where its rate is from (`source`): `plan` for the plan's own
    file, `hospital` for a hospital's own fee from its own file, and nothing
    when the part has no rate.

    `several_rates` says a part shown has more than one rate with that evidence,
    or at that source for a facility's fee, so someone should ask which they'd
    get, and `billers` lists the names of the billing groups behind the row
    (biller_places.biller_name), which at a hospital are the groups reading
    there. Names that match once punctuation and company endings are dropped are
    one group, under its lowest billing NPI's spelling, as a practice's name is.
    `providers` lists the clinicians Medicare shows billing the service for those
    billers, never the whole roster (#135 step 1): the ones CMS lists practicing
    in the building, or all of them at a biller's main place when it lists none
    at any of its places (service_sites.providers). It's empty when Medicare
    shows none, as at an imaging center it has no record of. An organization's
    own NPI isn't a clinician, and neither is a doctor with no Georgia NPPES
    record as a person. `specialties` is, for a family that asks which kind of
    doctor (families.yaml `asks_specialty`), the kinds (integration/specialties.yaml)
    the row's `providers` practice, in that file's order. Where they give none (a
    generic nurse practitioner, or no clinicians listed), it's the kinds of the
    NPPES specialty codes of the row's billers' own NPIs, the practice or
    organization behind the row. It's empty for any other family, and for a row
    where neither gives a kind."""
    name = "prices"
    key = ["network_id", "service_id", "place_id"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "place_id": "VARCHAR",
               "parts": 'STRUCT(part VARCHAR, max DOUBLE, "source" VARCHAR)[]', "several_rates": "BOOLEAN",
               "billers": "VARCHAR[]", "providers": "BIGINT[]", "cash_price": "DOUBLE",
               "specialties": "VARCHAR[]"}

    def select(self, data_dir):
        rank = f"list_position({list(EVIDENCE)!r}, r.evidence)"
        counts = "w.hospital OR w.on_site OR NOT b.on_site"
        return f"""
            WITH clinicians AS (
                SELECT d.network_id, d.service_id, d.place_id, list(DISTINCT d.npi ORDER BY d.npi) AS providers
                FROM (SELECT network_id, service_id, place_id, unnest(providers) AS npi FROM service_sites) d
                JOIN {NPPES.read(data_dir)} n ON n.npi = d.npi AND n.entity_type = 'individual'
                GROUP BY d.network_id, d.service_id, d.place_id
            ),
            kinds AS (
                SELECT network_id, service_id, place_id, list(specialty ORDER BY position) AS specialties
                FROM (SELECT DISTINCT c.network_id, c.service_id, c.place_id, k.specialty, s.position
                      FROM (SELECT network_id, service_id, place_id, unnest(providers) AS npi FROM clinicians) c
                      JOIN service_list sl ON sl.service_id = c.service_id
                      JOIN family_list f ON f.family = sl.family AND f.asks_specialty
                      JOIN {NPPES.read(data_dir)} n ON n.npi = c.npi
                      JOIN specialty_taxonomies k ON k.taxonomy_code = n.taxonomy_code
                      JOIN specialties s ON s.specialty = k.specialty)
                GROUP BY network_id, service_id, place_id
            ),
            biller_kinds AS (
                SELECT network_id, service_id, place_id, list(specialty ORDER BY position) AS specialties
                FROM (SELECT DISTINCT s.network_id, s.service_id, s.place_id, k.specialty, sp.position
                      FROM service_sites s
                      JOIN service_list sl ON sl.service_id = s.service_id
                      JOIN family_list f ON f.family = sl.family AND f.asks_specialty
                      JOIN {NPPES.read(data_dir)} n ON n.npi = try_cast(s.biller AS BIGINT)
                      JOIN specialty_taxonomies k ON k.taxonomy_code = n.taxonomy_code
                      JOIN specialties sp ON sp.specialty = k.specialty)
                GROUP BY network_id, service_id, place_id
            ),
            groups AS (
                SELECT network_id, service_id, place_id, list(name ORDER BY name) AS billers
                FROM (SELECT s.network_id, s.service_id, s.place_id, arg_min(b.biller_name, b.biller) AS name
                      FROM service_sites s JOIN biller_places b USING (biller, place_id)
                      GROUP BY s.network_id, s.service_id, s.place_id, {practice_name("b.biller_name")})
                GROUP BY network_id, service_id, place_id
            ),
            rated AS (
                SELECT r.network_id, r.service_id, b.place_id, b.kind, r.part, r.rate,
                       bool_or(r.on_site) OVER (PARTITION BY r.network_id, r.service_id, b.place_id) AS on_site,
                       {rank} = min({rank}) OVER (
                           PARTITION BY r.network_id, r.service_id, b.place_id, r.part) AS strongest
                FROM biller_rates r
                JOIN service_sites s USING (network_id, service_id, biller)
                JOIN biller_places b USING (biller, place_id)
                WHERE b.place_id IN (SELECT place_id FROM places)
            ),
            rates AS (
                SELECT network_id, service_id, place_id, part, max(rate) AS max, count(DISTINCT rate) AS rates
                FROM rated
                WHERE strongest
                GROUP BY network_id, service_id, place_id, part
            ),
            rows AS (
                SELECT network_id, service_id, place_id, any_value(kind) = 'hospital' AS hospital,
                       bool_or(on_site) AS on_site
                FROM rated
                GROUP BY network_id, service_id, place_id
                UNION ALL BY NAME
                SELECT f.network_id, f.service_id, f.place_id, p.kind = 'hospital' AS hospital, false AS on_site
                FROM facility_fees f JOIN places p USING (place_id)
                WHERE (f.network_id, f.service_id, f.place_id) NOT IN (
                    SELECT (network_id, service_id, place_id) FROM rated)
            ),
            choices AS (
                SELECT family, true AS hospital, 1 AS choice, generate_subscripts(hospital, 1) AS position,
                       unnest(hospital) AS part
                FROM family_list
                UNION ALL
                SELECT family, false, choice, generate_subscripts(parts, 1), unnest(parts)
                FROM (SELECT family, generate_subscripts(elsewhere, 1) AS choice, unnest(elsewhere) AS parts
                      FROM family_list)
            ),
            offered AS (
                SELECT w.network_id, w.service_id, w.place_id, w.hospital, c.choice, c.position, c.part,
                       CASE WHEN b.billing_class = 'institutional' THEN f.fee WHEN {counts} THEN r.max END AS max,
                       CASE WHEN b.billing_class = 'institutional' THEN f.rates WHEN {counts} THEN r.rates END
                           AS rates,
                       CASE WHEN b.billing_class = 'institutional' THEN f.source ELSE 'plan' END AS source
                FROM rows w
                JOIN service_list s USING (service_id)
                JOIN choices c ON c.family = s.family AND c.hospital = w.hospital
                JOIN family_bills b ON b.family = c.family AND b.part = c.part
                LEFT JOIN rates r ON r.network_id = w.network_id AND r.service_id = w.service_id
                                 AND r.place_id = w.place_id AND r.part = c.part
                LEFT JOIN facility_fees f ON f.network_id = w.network_id AND f.service_id = w.service_id
                                         AND f.place_id = w.place_id
            ),
            chosen AS (
                SELECT network_id, service_id, place_id, coalesce(min(choice) FILTER (WHERE whole), max(choice))
                    AS choice
                FROM (SELECT network_id, service_id, place_id, choice, bool_and(max IS NOT NULL) AS whole
                      FROM offered
                      GROUP BY network_id, service_id, place_id, choice)
                GROUP BY network_id, service_id, place_id
            ),
            shown AS (
                SELECT network_id, service_id, place_id, any_value(hospital) AS hospital,
                       list({{'part': part, 'max': max, 'source': CASE WHEN max IS NOT NULL THEN source END}}
                            ORDER BY position) AS parts,
                       bool_or(coalesce(rates, 0) > 1) AS several_rates
                FROM offered JOIN chosen USING (network_id, service_id, place_id, choice)
                GROUP BY network_id, service_id, place_id
                HAVING bool_or(max IS NOT NULL)
            )
            SELECT network_id, service_id, place_id, parts, several_rates,
                   coalesce(g.billers, []::VARCHAR[]) AS billers, coalesce(c.providers, []::BIGINT[]) AS providers,
                   CASE WHEN hospital THEN h.cash_price END AS cash_price,
                   coalesce(kn.specialties, bk.specialties, []::VARCHAR[]) AS specialties
            FROM shown
            LEFT JOIN groups g USING (network_id, service_id, place_id)
            LEFT JOIN clinicians c USING (network_id, service_id, place_id)
            LEFT JOIN kinds kn USING (network_id, service_id, place_id)
            LEFT JOIN biller_kinds bk USING (network_id, service_id, place_id)
            LEFT JOIN hospital_cash h USING (service_id, place_id)
        """

    def check(self, con, data_dir):
        """Every row's network, service and place is in its table, the service
        among those integration/services.yaml names."""
        for column, table in [("network_id", "networks"), ("service_id", "service_list"), ("place_id", "places")]:
            stray = con.execute(f"""
                SELECT {column} FROM prices WHERE {column} NOT IN (SELECT {column} FROM {table}) LIMIT 1
            """).fetchone()
            if stray:
                raise SystemExit(f"prices: {column} {stray[0]} isn't in {table}")

        self.note_unmapped(con, data_dir)

    def note_unmapped(self, con, data_dir):
        """Print the specialty labels of clinicians on rows of a family that asks
        which kind of doctor and count as none (integration/specialties.yaml), with
        how many places each is at, and the rows left with no kind at all."""
        asking = """FROM prices p JOIN service_list s USING (service_id)
                    JOIN family_list f ON f.family = s.family AND f.asks_specialty"""
        unmapped = con.execute(f"""
            SELECT coalesce(t.display_name, n.taxonomy_code, 'no taxonomy') AS label, count(DISTINCT d.place_id) AS places
            FROM (SELECT p.place_id, unnest(p.providers) AS npi {asking}) d
            JOIN {NPPES.read(data_dir)} n ON n.npi = d.npi
            LEFT JOIN {NUCC_TAXONOMY.read(data_dir)} t ON t.taxonomy_code = n.taxonomy_code
            WHERE n.taxonomy_code IS NULL OR n.taxonomy_code NOT IN (SELECT taxonomy_code FROM specialty_taxonomies)
            GROUP BY 1 ORDER BY 2 DESC, 1""").fetchall()
        if unmapped:
            print(f"  clinicians' specialties that count as no kind of doctor, with their places ({len(unmapped)}): "
                  + "; ".join(f"{label} {places:,}" for label, places in unmapped))
        none, total = con.execute(f"""
            SELECT count(*) FILTER (WHERE len(p.specialties) = 0), count(*) {asking}""").fetchone()
        if total:
            print(f"  rows of a family that asks which kind of doctor with no kind: {none:,} of {total:,}")
