"""Build-only: each rate a network's file lists for a service, by the biller it
covers, with the evidence that the biller does or reads that service."""
from integration.inputs.datasets import (MEDICAID_BILLING, MEDICARE_BILLING, NPPES, PAYER_RATE_GROUPS, PAYER_RATES, PAYER_ROSTERS,
                                         own_first, roster_biller)
from integration.table import Table

# Modifiers that make a rate a different bill from the service's own, or the bill
# in a case of its own, so a rate with one is left out, as is a rate that names
# two parts at once. The bill a rate is for is the professional bill in the
# service's family that names each of its modifiers but BODY_SITES
# (integration/families.yaml), and a rate with a modifier no family's bills or
# these lists name stops the build (BillerRates.check), so a new one is read
# before it is priced or dropped.
OTHER_BILLS = {
    "50": "both sides in one visit",
    "62": "two surgeons",
    "80": "an assistant surgeon",
    "AS": "a physician assistant or nurse assisting at surgery",
    "RR": "rented equipment",
    "NU": "new equipment",
    "UE": "used equipment",
    "22": "a procedure harder or longer than usual",
    "55": "only the care after a surgery",
    "25": "a separate visit on the day of a procedure",
    "27": "a second hospital outpatient visit the same day",
    "59": "a procedure distinct from another the same day",
    "53": "a procedure stopped before it was finished",
    "32": "a service someone other than the patient requires",
    "95": "a visit by live video",
    "GT": "a visit by live video",
    "GQ": "a visit by recorded video or images",
    "EP": "a child's checkup under Medicaid's screening program",
    "AH": "a clinical psychologist's rate",
    "AJ": "a clinical social worker's rate",
    "HO": "a clinician with a master's degree",
    "HP": "a clinician with a doctoral degree",
    "SA": "a nurse practitioner's service with a doctor",
    "CQ": "a physical therapist assistant's rate",
    "CO": "an occupational therapy assistant's rate",
    "GC": "a resident's service under a teaching physician",
    "GE": "a resident's service with no teaching physician present",
    "GF": "a nurse practitioner's or physician assistant's service in a critical access hospital",
    "FS": "a visit split between a doctor and a nurse practitioner or physician assistant",
    "Q5": "a substitute doctor under a reciprocal arrangement",
    "Q6": "a substitute doctor paid for the time",
    "93": "a visit by phone",
    "FQ": "a visit by audio only",
    "G0": "a telehealth visit for a stroke",
    "24": "an unrelated visit during the care after a surgery",
    "57": "a visit that decides on a major surgery",
    "58": "a planned later stage of a procedure",
    "78": "a return to the operating room during the care after a surgery",
    "79": "an unrelated procedure during the care after a surgery",
    "51": "a second procedure the same day",
    "52": "a service reduced from its usual extent",
    "54": "only the surgery, without the care before or after",
    "63": "a procedure on an infant under 4 kg",
    "74": "an outpatient procedure stopped after anesthesia",
    "76": "a procedure repeated by the same doctor",
    "77": "a procedure repeated by another doctor",
    "82": "an assistant surgeon where no resident is available",
    "91": "a lab test repeated the same day",
    "90": "a lab test sent to an outside lab",
    "XE": "a separate encounter the same day",
    "XP": "a different practitioner the same day",
    "XS": "a separate organ or structure the same day",
    "XU": "an unusual service apart from the main one",
    "33": "a service billed as preventive",
    "PT": "a screening colonoscopy that became diagnostic",
    "PD": "a diagnostic service within three days of a hospital stay",
    "FP": "a service under a family planning program",
    "TH": "an obstetric service before or after a birth",
    "HA": "a child or adolescent program",
    "HE": "a mental health program",
    "TR": "a school-based service outside the student's district",
    "AT": "a chiropractor's treatment of an acute condition",
    "AY": "a dialysis patient's service unrelated to dialysis",
    "Q1": "routine care in a clinical trial",
    "Q3": "a service for a live kidney donor",
    "Q7": "foot care with one class A finding",
    "Q8": "foot care with two class B findings",
    "Q9": "foot care with one class B and two class C findings",
    "SG": "a surgery center's facility fee",
    "BL": "blood acquired specially",
    "FY": "an X-ray taken on cassette-based equipment",
    "JZ": "a drug with none of it discarded",
    "GA": "a service the patient signed a waiver of liability for",
    "GY": "a service excluded from coverage by law",
    "GW": "a service unrelated to a hospice patient's terminal condition",
    "KX": "a service shown to meet a policy's requirements",
    "CG": "a service under a payer's policy criteria",
    "CR": "a service tied to a declared disaster",
    "GB": "a claim resubmitted under a payment demonstration",
    "U1": "a variant the payer defines",
    "U2": "a variant the payer defines",
    "U3": "a variant the payer defines",
    "U4": "a variant the payer defines",
    "U5": "a variant the payer defines",
    "U6": "a variant the payer defines",
    "UD": "a variant the payer defines",
}

# Modifiers that only say where on the body a service is done, which doesn't make
# it a different bill, so a rate's bill is found without them: a plan's file
# often prices a surgery on one side or digit under such a modifier alone.
BODY_SITES = {
    "RT": "the right side", "LT": "the left side",
    "E1": "the upper left eyelid", "E2": "the lower left eyelid",
    "E3": "the upper right eyelid", "E4": "the lower right eyelid",
    "FA": "the left thumb", "F1": "the left second finger", "F2": "the left third finger",
    "F3": "the left fourth finger", "F4": "the left fifth finger", "F5": "the right thumb",
    "F6": "the right second finger", "F7": "the right third finger", "F8": "the right fourth finger",
    "F9": "the right fifth finger",
    "TA": "the left great toe", "T1": "the left second toe", "T2": "the left third toe",
    "T3": "the left fourth toe", "T4": "the left fifth toe", "T5": "the right great toe",
    "T6": "the right second toe", "T7": "the right third toe", "T8": "the right fourth toe",
    "T9": "the right fifth toe",
}


def _bill_modifiers(modifier):
    """SQL for the modifiers a rate line's bill is found by: each of `modifier`'s,
    which joins them with "|", but BODY_SITES, or [""] where none is left. A line
    is for the bill that lists every one of them."""
    sites = ", ".join(f"'{m}'" for m in BODY_SITES)
    kept = f"list_filter(string_split(coalesce({modifier}, ''), '|'), m -> m NOT IN ({sites}))"
    return f"CASE WHEN len({kept}) = 0 THEN [''] ELSE {kept} END"

# Why a rate is believed to be one a patient would get, strongest first. prices
# shows a place's rate with the strongest evidence, so the order is a product rule.
#   billed_roster   Medicare or Medicaid shows a doctor on the rate's own roster billing the service
#   billed_biller   Medicare or Medicaid shows one behind the biller, though not on this rate's roster
#   registered      the biller is registered as a center for that kind of service
EVIDENCE = ("billed_roster", "billed_biller", "registered")

# The rates the build reads: a fee-for-service dollar amount for an outpatient
# professional bill.
PRICED = """r.billing_class = 'professional' AND r.setting IN ('outpatient', 'both')
              AND r.negotiation_arrangement = 'ffs'
              AND r.negotiated_type IN ('fee schedule', 'negotiated')"""


# The families whose `elsewhere` offers the institutional bill, so a facility bills
# its own fee for them.
OWN_FEE_FAMILIES = """(SELECT f.family FROM family_list f
                       JOIN family_bills b ON b.family = f.family AND b.billing_class = 'institutional'
                       WHERE list_contains(flatten(f.elsewhere), b.part))"""


def _rank(place_of_service):
    """SQL for a rate line's place-of-service rank among `place_of_service`, the
    codes a bill reads, best first: 0 when the line names no place, CSTM-00 or the
    first code, else the position of the first code it names, else one past the
    last. An entry joining codes with "|" is named by a line that names any of
    them, and they rank equal."""
    names = (f"list_transform({place_of_service}, p -> "
             f"list_has_any(string_split(r.service_code, '|'), string_split(p, '|')))")
    return (f"CASE WHEN coalesce(r.service_code, '') IN ('', 'CSTM-00') THEN 0 "
            f"ELSE coalesce(list_position({names}, true) - 1, len({place_of_service})) END")


def covered(data_dir):
    """SQL for the CTEs that end in `covered`: each kept rate with the biller and
    each doctor (`npi`) on the rosters it covers whom Medicare or Medicaid shows billing the
    service, or none, and `billed`, the doctors Medicare or Medicaid shows billing each
    service. biller_doctors reads them too. A
    rate under the file's shared labels is kept only where the network's own labels
    have none for the biller, service and bill, and a placeholder doesn't count."""
    return f"""
        lines AS (
            SELECT r.network_id, s.service_id, r.group_set_id, b.part, r.negotiated_rate AS rate, r.own_label,
                   b.place_of_service IS NOT NULL AS own_places,
                   {_rank("coalesce(b.place_of_service, f.place_of_service)")} AS rank,
                   len(coalesce(b.place_of_service, f.place_of_service)) AS places
            FROM {PAYER_RATES.read(data_dir)} r
            JOIN service_list s ON s.billing_code = r.billing_code AND list_contains(s.code_types, r.billing_code_type)
            JOIN family_list f USING (family)
            JOIN family_bills b
              ON b.family = s.family AND b.billing_class = 'professional'
              AND list_has_all(b.modifiers, {_bill_modifiers("r.modifier")})
            WHERE {PRICED}
        ),
        -- A family's place of service ranks every line of a service together, the
        -- best line winning even when it names none of the codes. A bill with its
        -- own place_of_service ranks its own lines, and only those naming one of
        -- its codes count.
        kept AS (
            SELECT * EXCLUDE (own_places, places) FROM lines
            WHERE NOT own_places OR rank < places
            QUALIFY rank = min(rank) OVER (PARTITION BY network_id, service_id, CASE WHEN own_places THEN part END)
        ),
        medians AS (
            SELECT network_id, service_id, part, median(rate) AS median FROM kept
            GROUP BY network_id, service_id, part
        ),
        -- The centers and facilities billing their own fee for a service, whose
        -- professional lines price no part of it.
        own_fee AS (
            SELECT DISTINCT v.service_id, n.npi
            FROM service_list v
            JOIN (SELECT family, taxonomy_code FROM centers
                  UNION SELECT f.family, t.taxonomy_code FROM family_list f, facilities t) c USING (family)
            JOIN {NPPES.read(data_dir)} n ON n.taxonomy_code = c.taxonomy_code
            WHERE v.family IN {OWN_FEE_FAMILIES}
        ),
        -- The doctors Medicare or Medicaid shows billing each service: its own code
        -- and any of its family's evidence_codes, and none where the family names [].
        evidence AS (
            SELECT DISTINCT s.service_id, len(f.elsewhere) = 1 AS one_way,
                   unnest(list_concat([s.billing_code], coalesce(f.evidence_codes, []::VARCHAR[]))) AS code
            FROM service_list s JOIN family_list f USING (family)
            WHERE coalesce(len(f.evidence_codes), 1) > 0
        ),
        -- Medicaid doesn't say where. Where a family bills a service one way, such as
        -- a visit, a Medicaid row is in an office unless Medicare shows the doctor
        -- billing that code only elsewhere. Where it bills it two ways, such as a scan
        -- and its reading, Medicaid can't say which was billed, so its row is never in
        -- an office.
        medicare_office AS (
            SELECT npi, hcpcs_cd, bool_or(place_of_service = 'O') AS in_office
            FROM {MEDICARE_BILLING.read(data_dir)}
            GROUP BY npi, hcpcs_cd
        ),
        billed AS (
            SELECT npi, service_id, bool_or(in_office) AS in_office
            FROM (SELECT m.npi, e.service_id, m.place_of_service = 'O' AS in_office
                  FROM {MEDICARE_BILLING.read(data_dir)} m
                  JOIN evidence e ON e.code = m.hcpcs_cd
                  UNION ALL
                  SELECT d.npi, e.service_id,
                         e.one_way AND coalesce(o.in_office, true)
                  FROM {MEDICAID_BILLING.read(data_dir)} d
                  JOIN evidence e ON e.code = d.hcpcs_cd
                  LEFT JOIN medicare_office o ON o.npi = d.npi AND o.hcpcs_cd = d.hcpcs_cd)
            GROUP BY npi, service_id
        ),
        -- The doctors who can count for a service: Medicare or Medicaid shows them billing it
        -- (`billed`), or NPPES registers them as a center of its family. A roster
        -- row billed under a tax ID bills as its own NPI, with itself as its only
        -- doctor, so it prices a service only where that NPI is one of these.
        candidates AS (
            SELECT npi, service_id, bool_or(billed) AS billed
            FROM (SELECT npi, service_id, true AS billed FROM billed
                  UNION ALL
                  SELECT n.npi, s.service_id, false
                  FROM service_list s
                  JOIN centers t USING (family)
                  JOIN {NPPES.read(data_dir)} n ON n.taxonomy_code = t.taxonomy_code)
            GROUP BY npi, service_id
        ),
        -- A rate's doctors are kept only where Medicare or Medicaid shows them billing the
        -- service, the only ones biller_doctors and biller_rates read, and its
        -- others are one row with no `npi`, so a large roster isn't carried
        -- doctor by doctor.
        real AS (
            SELECT DISTINCT k.network_id, k.service_id, k.part, k.rate, {roster_biller('p')} AS biller,
                   CASE WHEN t.billed THEN p.npi END AS npi, k.own_label
            FROM kept k
            JOIN medians m USING (network_id, service_id, part)
            JOIN {PAYER_RATE_GROUPS.read(data_dir)} g USING (network_id, group_set_id)
            JOIN {PAYER_ROSTERS.read(data_dir)} p USING (network_id, provider_group_id)
            LEFT JOIN candidates t ON t.npi = p.npi AND t.service_id = k.service_id
            ANTI JOIN own_fee o ON o.service_id = k.service_id AND o.npi = TRY_CAST({roster_biller('p')} AS BIGINT)
            WHERE k.rate > greatest(1, 0.05 * m.median) AND (p.tin_type = 'npi' OR t.npi IS NOT NULL)
        ),
        covered AS (
            SELECT DISTINCT network_id, service_id, part, rate, biller, npi FROM real
            QUALIFY {own_first("network_id", "service_id", "part", "biller")}
        )"""


class BillerRates(Table):
    """Rates are the outpatient professional ones: fee-for-service, a fee schedule
    or negotiated dollar amount, for an outpatient visit, each for the bill in the
    service's family that names its modifier (family_bills). A service keeps only its
    lines for the best place of service its family reads that any of them names
    on the network (`place_of_service` in integration/families.yaml, an office
    unless it says otherwise), except that a bill naming its own `place_of_service`
    keeps only the lines naming one of its codes, the best of them, so a line
    naming two of its codes' places counts for both bills, and a placeholder rate, at most $1 or 5% of the
    median for that service and part on the network, is dropped. A rate under the
    file's shared labels is used only where the network's own labels have none
    for the biller, service and bill.

    A biller NPPES registers as a center of a family, or as a facility, whose
    `elsewhere` offers the institutional bill has no professional rate for its
    services: the fee schedule on its NPI is not a doctor's fee, and its own fee
    is the institutional bill (integration/inputs/facility_fees.py).

    A rate's roster is the provider groups its group set lists, on the rate's
    network.

    A biller appears for a service if Medicare or Medicaid shows a doctor billing it for that
    biller (biller_doctors), or if its NPPES primary specialty code registers it
    as a center doing the services of the service's family (centers), because
    Medicare's file often misses those centers. `registered` says it's such a
    center, and `on_site` that it is, or that one of its doctors billed the
    service in an office, so it does the service where it is. Otherwise the
    biller only bills the parts done elsewhere, such as a scan's reading (the
    2026-11-04 decisions on #135).

    `evidence` is the strongest of EVIDENCE behind the rate. A biller often sits
    under several fee schedules, each with its own roster, and the file doesn't
    say which a patient gets, so a rate whose own roster has a doctor Medicare or
    Medicaid shows billing the service outranks one whose roster has none (#191)."""
    name = "biller_rates"
    key = ["network_id", "service_id", "biller", "part", "rate"]
    columns = {"network_id": "VARCHAR", "service_id": "VARCHAR", "biller": "VARCHAR",
               "part": "VARCHAR", "rate": "DOUBLE", "on_site": "BOOLEAN", "registered": "BOOLEAN",
               "evidence": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            WITH {covered(data_dir)},
            on_roster AS (
                SELECT DISTINCT c.network_id, c.service_id, c.biller, c.part, c.rate
                FROM covered c
                JOIN biller_doctors d USING (network_id, service_id, biller, npi)
            ),
            behind AS (
                SELECT network_id, service_id, biller, bool_or(in_office) AS in_office
                FROM biller_doctors
                GROUP BY network_id, service_id, biller
            ),
            registered AS (
                SELECT DISTINCT c.network_id, c.service_id, c.biller
                FROM covered c
                JOIN service_list s USING (service_id)
                JOIN {NPPES.read(data_dir)} n ON n.npi = TRY_CAST(c.biller AS BIGINT)
                JOIN centers t ON t.family = s.family AND t.taxonomy_code = n.taxonomy_code
            )
            SELECT DISTINCT c.network_id, c.service_id, c.biller, c.part, c.rate,
                   r.biller IS NOT NULL OR coalesce(e.in_office, false) AS on_site,
                   r.biller IS NOT NULL AS registered,
                   CASE WHEN o.biller IS NOT NULL THEN 'billed_roster'
                        WHEN e.biller IS NOT NULL THEN 'billed_biller'
                        ELSE 'registered' END AS evidence
            FROM covered c
            LEFT JOIN on_roster o USING (network_id, service_id, biller, part, rate)
            LEFT JOIN behind e USING (network_id, service_id, biller)
            LEFT JOIN registered r USING (network_id, service_id, biller)
            WHERE e.biller IS NOT NULL OR r.biller IS NOT NULL
        """

    def check(self, con, data_dir):
        """Every modifier on a service's rates is one the build has a rule for.

        A biller is found by its NPI: a roster row's billing NPI, or, where it bills
        under a tax ID (EIN), its own NPI (datasets.roster_biller). A billing ID of
        any other type, or an EIN of the wrong shape, stops the build here."""
        bills = [m for (m,) in con.execute(
            "SELECT DISTINCT unnest(modifiers) FROM family_bills WHERE billing_class = 'professional'").fetchall()]
        known = ", ".join(f"'{m}'" for m in (*bills, *OTHER_BILLS, *BODY_SITES))
        unknown = [m for (m,) in con.execute(f"""
            SELECT DISTINCT modifier
            FROM (SELECT unnest(string_split(coalesce(r.modifier, ''), '|')) AS modifier
                  FROM {PAYER_RATES.read(data_dir)} r
                  JOIN service_list s ON s.billing_code = r.billing_code AND list_contains(s.code_types, r.billing_code_type)
                  WHERE {PRICED})
            WHERE modifier NOT IN ({known})
            ORDER BY modifier
        """).fetchall()]
        if unknown:
            raise SystemExit(f"biller_rates: rates for a listed service carry modifiers {unknown}, which the build "
                             f"has no rule for; add each to a family's bills in integration/families.yaml or to "
                             f"OTHER_BILLS in integration/inputs/biller_rates.py")
        other = con.execute(f"""
            SELECT tin_type, count(*) FROM {PAYER_ROSTERS.read(data_dir)}
            WHERE tin_type IS DISTINCT FROM 'npi' AND tin_type IS DISTINCT FROM 'ein'
            GROUP BY tin_type ORDER BY 2 DESC
        """).fetchone()
        if other:
            raise SystemExit(f"biller_rates: {other[1]:,} roster rows bill under a {other[0]!r} ID, which is "
                             f"neither an NPI nor a tax ID (ein)")
        bad = con.execute(f"""
            SELECT tin_value, count(*) FROM {PAYER_ROSTERS.read(data_dir)}
            WHERE tin_type = 'ein' AND NOT regexp_full_match(coalesce(tin_value, ''), '[0-9]{{2}}-?[0-9]{{7}}')
            GROUP BY tin_value ORDER BY 2 DESC
        """).fetchone()
        if bad:
            raise SystemExit(f"biller_rates: {bad[1]:,} roster rows bill under the tax ID {bad[0]!r}, which "
                             f"is not the shape NN-NNNNNNN or NNNNNNNNN")
