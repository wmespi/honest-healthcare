"""Build-only: the model in integration/families.yaml, which says what a service's
bills are, one table per part of it: the benefits a member's share falls under
(benefits), the kinds of place (kinds), each family of services (family_list),
each family's bills (family_bills), and the specialty codes that register a
biller as a center doing a family's services (centers)."""
import os
import re

import yaml

from integration.inputs.datasets import NUCC_TAXONOMY, SHOPPABLE_SERVICES
from integration.table import Table

FAMILIES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "families.yaml")

# A key the build and the screen use: a family, a part, a benefit or a kind.
KEY = re.compile(r"[a-z][a-z0-9_]*")
FAMILY_FIELDS = {"family", "name", "groups", "except", "codes", "benefits", "about", "could_change", "asks", "bills",
                 "hospital", "elsewhere", "centers", "evidence_codes", "place_of_service"}
BILL_FIELDS = {"part", "label", "billing_class", "modifier", "on_site", "benefits", "missing"}
MISSING_FIELDS = {"note", "explain", "explain_at_hospital"}
# The place-of-service codes a family reads when it doesn't say: an office.
OFFICE = ["11"]


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def _codes(value, what, stop, empty=False):
    """`value` as a list of codes, each a quoted string, or stop the build."""
    if not isinstance(value, list) or not all(_text(c) for c in value) or (not value and not empty):
        stop(f"{what} must be a list of quoted codes")
    return value


def benefit_keys(value, declared, what, stop):
    """`value` as a list of the benefits `declared`, or stop the build."""
    if not isinstance(value, list) or not value:
        stop(f"{what} names no benefits")
    unknown = [b for b in value if b not in declared]
    if unknown:
        stop(f"{what} names benefits {unknown}, which benefits doesn't declare")
    return value


def read_families(path=FAMILIES_PATH):
    """integration/families.yaml, each family with its defaults filled in and each
    bill with the benefits it falls under, or stop the build on anything in it
    the build can't use."""
    with open(path, encoding="utf-8") as f:
        model = yaml.safe_load(f) or {}

    def stop(why):
        raise SystemExit(f"{path}: {why}")

    extra = sorted(set(model) - {"shared", "benefits", "kinds", "families"})
    if extra:
        stop(f"has sections the build doesn't use: {extra}")
    benefits, kinds = model.get("benefits") or {}, model.get("kinds") or {}
    for key, benefit in benefits.items():
        if (not KEY.fullmatch(str(key)) or not isinstance(benefit, dict) or set(benefit) != {"name", "label"}
                or not all(_text(v) for v in benefit.values())):
            stop(f"benefit {key!r} needs a lower-case key, a name and a label, and nothing else")
    for key, label in kinds.items():
        if not KEY.fullmatch(str(key)) or not _text(label):
            stop(f"kind {key!r} needs a lower-case key and a label")
    missing = sorted({"hospital", "practice"} - set(kinds))
    if missing:
        stop(f"kinds doesn't declare {missing}, the kinds of place that aren't centers")
    families, center_kinds = [], {}
    for position, f in enumerate(model.get("families") or [], 1):
        family = f.get("family")
        if not KEY.fullmatch(str(family)) or family == "shared":
            stop(f"family {family!r} isn't a lower-case key such as mri")
        extra = sorted(set(f) - FAMILY_FIELDS)
        if extra:
            stop(f"{family} has fields the build doesn't use: {extra}")
        if family in (g["family"] for g in families):
            stop(f"family {family} repeats")
        bills = _bills(f, benefits, stop)
        parts = [b["part"] for b in bills]
        institutional = [b["part"] for b in bills if b["billing_class"] == "institutional"]
        hospital, elsewhere = f.get("hospital"), f.get("elsewhere") or []
        for where, listed in ([("hospital", hospital)] if hospital is not None else []) + [
                ("elsewhere", choice) for choice in elsewhere]:
            if not isinstance(listed, list) or not listed or len(set(listed)) != len(listed):
                stop(f"{family}'s {where} needs a list of bills, none repeated")
            for part in listed:
                if part not in parts:
                    stop(f"{family}'s {where} names {part!r}, which isn't one of its bills")
                if where == "elsewhere" and part in institutional:
                    stop(f"{family}'s elsewhere names {part}, the hospital's own fee, which only a hospital bills")
        centers = f.get("centers") or {}
        for code, kind in centers.items():
            if kind not in kinds:
                stop(f"{family}'s center {code} is a {kind!r}, which kinds doesn't declare")
            if kind == "hospital":
                stop(f"{family}'s center {code} is a hospital, but only a hospital's own building is one")
            other = center_kinds.setdefault(code, (family, kind))
            if other[1] != kind:
                stop(f"center {code} is a {kind} for {family} but a {other[1]} for {other[0]}")
        evidence = f.get("evidence_codes")
        families.append({
            "family": family, "name": f.get("name"), "position": position,
            "groups": _codes(f.get("groups") or [], f"{family}'s groups", stop, empty=True),
            "except": _codes(f.get("except") or [], f"{family}'s except", stop, empty=True),
            "codes": _codes(f.get("codes") or [], f"{family}'s codes", stop, empty=True),
            "about": f.get("about"), "could_change": f.get("could_change"), "asks": f.get("asks"),
            "bills": bills, "hospital": hospital, "elsewhere": elsewhere, "centers": centers,
            "evidence_codes": None if evidence is None else _codes(evidence, f"{family}'s evidence_codes", stop,
                                                                   empty=True),
            "place_of_service": _codes(f.get("place_of_service", OFFICE), f"{family}'s place_of_service", stop),
        })
        if not _text(families[-1]["name"]):
            stop(f"{family} has no name")
    if not families:
        stop("lists no families")
    return {"benefits": benefits, "kinds": kinds, "families": families}


def _bills(f, benefits, stop):
    """Family `f`'s bills, in order, each with the benefits it falls under: its
    own, else the family's."""
    family, bills, modifiers = f["family"], [], {}
    for position, b in enumerate(f.get("bills") or [], 1):
        part, billing_class = b.get("part"), b.get("billing_class")
        extra = sorted(set(b) - BILL_FIELDS)
        if extra:
            stop(f"{family}'s {part} bill has fields the build doesn't use: {extra}")
        if not KEY.fullmatch(str(part)):
            stop(f"{family} has a bill whose part {part!r} isn't a lower-case key such as reading")
        if part in (c["part"] for c in bills):
            stop(f"{family} has two {part} bills")
        if not _text(b.get("label")):
            stop(f"{family}'s {part} bill has no label")
        listed = []
        if billing_class == "professional":
            listed = b.get("modifier")
            listed = [listed] if isinstance(listed, str) else listed
            if not (isinstance(listed, list) and listed and all(isinstance(m, str) for m in listed)
                    and len(set(listed)) == len(listed)):
                stop(f"{family}'s {part} bill is professional, so it needs a quoted modifier, \"\" for none, "
                     f"or a list of them, none repeated")
            for modifier in listed:
                if modifier in modifiers:
                    stop(f"{family}'s {modifiers[modifier]} and {part} bills both have modifier {modifier!r}")
                modifiers[modifier] = part
        elif billing_class == "institutional":
            if "modifier" in b or "on_site" in b:
                stop(f"{family}'s {part} bill is the hospital's own fee, so it has no modifier or on_site")
            if any(c["billing_class"] == "institutional" for c in bills):
                stop(f"{family} has two institutional bills, but a hospital bills one fee of its own")
        else:
            stop(f"{family}'s {part} bill has billing_class {billing_class!r}, not professional or institutional")
        missing = b.get("missing") or {}
        if set(missing) - MISSING_FIELDS or (missing and not (_text(missing.get("note"))
                                                               and _text(missing.get("explain")))):
            stop(f"{family}'s {part} bill: missing needs a note and an explain, and may have an explain_at_hospital")
        bills.append({
            "part": part, "position": position, "label": b["label"], "billing_class": billing_class,
            "modifiers": listed, "on_site": bool(b.get("on_site", False)),
            "benefits": benefit_keys(b.get("benefits", f.get("benefits")), benefits,
                                     f"{family}'s {part} bill", stop),
            "missing_note": missing.get("note"), "missing_explain": missing.get("explain"),
            "missing_explain_hospital": missing.get("explain_at_hospital"),
        })
    if not bills:
        stop(f"{family} has no bills")
    return bills


class FromYaml(Table):
    """A build-only table made from a YAML file, not a SELECT: `rows` gives its
    rows in its columns' order, and they go in as parameters."""

    def __init__(self, path=FAMILIES_PATH):
        self.path = path

    def rows(self):
        raise NotImplementedError

    def fill(self, con, data_dir):
        con.execute(f"CREATE TEMP TABLE {self.name} ("
                    + ", ".join(f"{c} {t}" for c, t in self.columns.items()) + ")")
        rows = self.rows()
        if rows:
            con.executemany(f"INSERT INTO {self.name} VALUES ({', '.join('?' * len(self.columns))})", rows)


class Benefits(FromYaml):
    """Each benefit a member's share can fall under: our key (`benefit`), its
    `name` in Georgia's cost-sharing file and the `label` the screen shows."""
    name = "benefits"
    key = ["benefit"]
    columns = {"benefit": "VARCHAR", "name": "VARCHAR", "label": "VARCHAR"}

    def rows(self):
        return [[k, b["name"], b["label"]] for k, b in read_families(self.path)["benefits"].items()]


class Kinds(FromYaml):
    """Each kind of place, with what the screen calls it (`name`) and its
    priority (`position`, 1 first): a place whose billers are registered as
    centers of two kinds is the one with the lower position."""
    name = "kinds"
    key = ["kind"]
    columns = {"kind": "VARCHAR", "name": "VARCHAR", "position": "INTEGER"}

    def rows(self):
        return [[k, label, i] for i, (k, label) in enumerate(read_families(self.path)["kinds"].items(), 1)]


class FamilyList(FromYaml):
    """One row per family, in the order the screen lists them (`position`), with
    the RBCS names and codes that put a shoppable code in it (members), its
    bills at a hospital (`hospital`, empty for a family not shown at hospitals)
    and elsewhere (`elsewhere`, alternatives in order), the Medicare codes that
    show a doctor does its services (`evidence_codes`, empty for each service's
    own code), and the place-of-service codes it reads, best first."""
    name = "family_list"
    key = ["family"]
    columns = {"family": "VARCHAR", "name": "VARCHAR", "position": "INTEGER", "groups": "VARCHAR[]",
               "except_groups": "VARCHAR[]", "codes": "VARCHAR[]", "hospital": "VARCHAR[]",
               "elsewhere": "VARCHAR[][]", "evidence_codes": "VARCHAR[]", "place_of_service": "VARCHAR[]"}

    def rows(self):
        return [[f["family"], f["name"], f["position"], f["groups"], f["except"], f["codes"], f["hospital"],
                 f["elsewhere"], f["evidence_codes"], f["place_of_service"]]
                for f in read_families(self.path)["families"]]


class FamilyBills(FromYaml):
    """One row per family and bill, in the family's order (`position`). A
    `professional` bill is priced from the plan's rates with one of its
    `modifiers` ("" is a rate with none), and
    `on_site` says only a place that does the service bills it; the
    `institutional` one is the hospital's own fee (hospital_fees). `benefits` is
    what the member's share for it falls under, unless a service names its own
    (service_list.SERVICE_BILLS)."""
    name = "family_bills"
    key = ["family", "part"]
    columns = {"family": "VARCHAR", "part": "VARCHAR", "position": "INTEGER", "label": "VARCHAR",
               "billing_class": "VARCHAR", "modifiers": "VARCHAR[]", "on_site": "BOOLEAN", "benefits": "VARCHAR[]",
               "missing_note": "VARCHAR", "missing_explain": "VARCHAR", "missing_explain_hospital": "VARCHAR"}

    def rows(self):
        return [[f["family"], *(b[c] for c in list(self.columns)[1:])]
                for f in read_families(self.path)["families"] for b in f["bills"]]


class Centers(FromYaml):
    """One row per family and NPPES primary specialty code that registers a
    biller as a center doing the family's services, with its kind of place.
    The build stops if NUCC stops listing one, so a retired code can't drop out
    silently."""
    name = "centers"
    key = ["family", "taxonomy_code"]
    columns = {"family": "VARCHAR", "taxonomy_code": "VARCHAR", "kind": "VARCHAR"}

    def rows(self):
        return [[f["family"], code, kind] for f in read_families(self.path)["families"]
                for code, kind in f["centers"].items()]

    def check(self, con, data_dir):
        missing = [c for (c,) in con.execute(f"""
            SELECT DISTINCT taxonomy_code FROM centers
            WHERE taxonomy_code NOT IN (SELECT taxonomy_code FROM {NUCC_TAXONOMY.read(data_dir)})
            ORDER BY 1""").fetchall()]
        if missing:
            raise SystemExit(f"centers: NUCC no longer lists {missing}; decide what replaces them")


def members(data_dir):
    """SQL for each family's shoppable codes (`family`, `billing_code`): the CPT
    and HCPCS codes whose RBCS subcategory or family is one of its groups and
    neither is one of its exceptions, plus the codes it lists."""
    return f"""
        SELECT f.family, x.billing_code
        FROM family_list f, {SHOPPABLE_SERVICES.read(data_dir)} x
        WHERE x.billing_code_type IN ('CPT', 'HCPCS')
          AND (list_contains(f.codes, x.billing_code)
               OR (list_has_any(f.groups, [x.rbcs_subcategory, x.rbcs_family])
                   AND NOT list_has_any(f.except_groups, [x.rbcs_subcategory, x.rbcs_family])))"""
