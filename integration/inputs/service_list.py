"""Build-only: one row per service in integration/services.yaml, with its family
and what the screen says about it."""
import os

import yaml

from integration.inputs.biller_rates import PRICED
from integration.inputs.datasets import PAYER_RATES, SHOPPABLE_SERVICES
from integration.inputs.families import FAMILIES_PATH, FromYaml, benefit_keys, members, read_families

SERVICES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "services.yaml")
SERVICE_FIELDS = {"service_id", "billing_code", "name", "keywords", "about", "could_change", "asks", "benefits",
                  "preventive_note"}

# Each named service's bills, from its family (family_bills), each with the
# benefits the member's share for it falls under: the service's own, else the
# bill's.
SERVICE_BILLS = """
    SELECT s.service_id, b.part, b.position, b.label, b.billing_class, coalesce(s.benefits, b.benefits) AS benefits,
           b.missing_note, b.missing_explain, b.missing_explain_hospital
    FROM service_list s JOIN family_bills b USING (family)"""


def read_services(path=SERVICES_PATH, families_path=FAMILIES_PATH):
    """Each service in integration/services.yaml, as a dict of its fields with its
    `family` and the family's text wherever it doesn't say its own, or stop the
    build on a family, a field or a benefit the build doesn't know."""
    model = read_families(families_path)
    families = {f["family"]: f for f in model["families"]}
    with open(path, encoding="utf-8") as f:
        listed = yaml.safe_load(f) or {}

    def stop(why):
        raise SystemExit(f"{path}: {why}")

    services = []
    for family, entries in listed.items():
        if family == "shared":
            continue
        if family not in families:
            stop(f"lists services under {family!r}, which {families_path} doesn't declare")
        for s in entries or []:
            unknown = sorted(set(s) - SERVICE_FIELDS)
            if unknown:
                stop(f"{s.get('service_id')} has fields the screen doesn't use: {unknown}")
            own = s.get("benefits")
            if own is not None:
                benefit_keys(own, model["benefits"], s.get("service_id"), stop)
            text = {k: families[family][k] if s.get(k) is None else s[k] for k in ("about", "could_change", "asks")}
            services.append({**s, **text, "family": family})
    return services


class ServiceList(FromYaml):
    """A `service_id` is a lower-case slug and appears once, and a `billing_code`
    is five characters, one of CMS's shoppable services in the service's family
    (families.members), and belongs to one service in that family, though it
    may be a service in each of two. Every service has a name, keywords and
    text about it, and at least one thing that could change the bill and one
    question to ask, its own or its family's. `benefits` is the service's own,
    for every one of its bills, and empty where it names none."""
    name = "service_list"
    key = ["service_id"]
    columns = {"service_id": "VARCHAR", "billing_code": "VARCHAR", "family": "VARCHAR", "name": "VARCHAR",
               "keywords": "VARCHAR", "about": "VARCHAR", "could_change": "VARCHAR[]", "asks": "VARCHAR[]",
               "preventive_note": "VARCHAR", "benefits": "VARCHAR[]"}

    def __init__(self, path=SERVICES_PATH, families_path=FAMILIES_PATH):
        self.path, self.families_path = path, families_path

    def rows(self):
        services = read_services(self.path, self.families_path)
        if not services:
            raise SystemExit(f"{self.path} lists no services")
        return [[s.get(c) for c in self.columns] for s in services]

    def check(self, con, data_dir):
        """Stop the build on a service that breaks a rule above. Not a stop: list
        each family's shoppable codes that the plans price but no service names,
        as candidates for one."""
        bad = con.execute("""
            SELECT service_id FROM service_list
            WHERE NOT regexp_full_match(service_id, '[a-z0-9]+(-[a-z0-9]+)*')
            ORDER BY service_id
        """).fetchone()
        if bad:
            raise SystemExit(f"service_list: {bad[0]!r} isn't a lower-case slug such as mri-lower-back")
        bad = con.execute("""
            SELECT service_id, billing_code FROM service_list
            WHERE NOT regexp_full_match(coalesce(billing_code, ''), '[0-9A-Z]{5}')
            ORDER BY service_id
        """).fetchone()
        if bad:
            raise SystemExit(f"service_list: {bad[0]}'s billing_code {bad[1]!r} isn't a five-character code")
        shared = con.execute("""
            SELECT family, billing_code, list(service_id ORDER BY service_id) FROM service_list
            GROUP BY family, billing_code HAVING count(*) > 1 ORDER BY family, billing_code
        """).fetchone()
        if shared:
            raise SystemExit(f"service_list: {shared[2]} all bill {shared[1]} in {shared[0]}, but a code is one "
                             f"service in a family")
        empty = con.execute("""
            SELECT service_id, field FROM (
                SELECT service_id, 'name' AS field FROM service_list WHERE coalesce(trim(name), '') = ''
                UNION ALL SELECT service_id, 'keywords' FROM service_list WHERE coalesce(trim(keywords), '') = ''
                UNION ALL SELECT service_id, 'about' FROM service_list WHERE coalesce(trim(about), '') = ''
                UNION ALL SELECT service_id, 'could_change' FROM service_list
                    WHERE coalesce(len(could_change), 0) = 0
                       OR len(list_filter(could_change, x -> coalesce(trim(x), '') = '')) > 0
                UNION ALL SELECT service_id, 'asks' FROM service_list
                    WHERE coalesce(len(asks), 0) = 0
                       OR len(list_filter(asks, x -> coalesce(trim(x), '') = '')) > 0
                UNION ALL SELECT service_id, 'preventive_note' FROM service_list
                    WHERE preventive_note IS NOT NULL AND trim(preventive_note) = '')
            ORDER BY service_id, field
        """).fetchone()
        if empty:
            raise SystemExit(f"service_list: {empty[0]} has an empty {empty[1]}")
        stray = con.execute(f"""
            SELECT s.service_id, s.billing_code, s.family, x.rbcs_subcategory, x.rbcs_family
            FROM service_list s
            LEFT JOIN {SHOPPABLE_SERVICES.read(data_dir)} x
              ON x.billing_code = s.billing_code AND x.billing_code_type IN ('CPT', 'HCPCS')
            WHERE (s.family, s.billing_code) NOT IN (SELECT (family, billing_code) FROM ({members(data_dir)}))
            ORDER BY s.service_id
        """).fetchone()
        if stray and stray[3] is None:
            raise SystemExit(f"service_list: {stray[0]}'s billing_code {stray[1]} isn't one of CMS's shoppable "
                             f"services")
        if stray:
            raise SystemExit(f"service_list: {stray[0]}'s billing_code {stray[1]} is {stray[3]!r}, "
                             f"{stray[4]!r} in CMS's classification, which isn't in {stray[2]}")
        for family, codes in con.execute(f"""
            SELECT m.family, list(DISTINCT m.billing_code ORDER BY m.billing_code)
            FROM ({members(data_dir)}) m JOIN family_list f USING (family)
            WHERE m.billing_code IN (SELECT r.billing_code FROM {PAYER_RATES.read(data_dir)} r WHERE {PRICED})
              AND (m.family, m.billing_code) NOT IN (SELECT (family, billing_code) FROM service_list)
            GROUP BY m.family, f.position ORDER BY f.position
        """).fetchall():
            print(f"  {family}: shoppable codes the plans price that no service in services.yaml names "
                  f"({len(codes)}): {', '.join(codes)}")
