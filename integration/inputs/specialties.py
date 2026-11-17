"""Build-only: the kinds of doctor in integration/specialties.yaml, which someone
picks for a family that asks (families.yaml `asks_specialty`): each kind
(specialties) and the NUCC codes that count as it (specialty_taxonomies)."""
import os

import yaml

from integration.inputs.datasets import NUCC_TAXONOMY
from integration.inputs.families import FAMILIES_PATH, KEY, FromYaml, _text, read_families

SPECIALTIES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "specialties.yaml")
SPECIALTY_FIELDS = {"specialty", "name", "benefit", "taxonomies"}
BENEFITS = {"primary_care", "specialist"}


def read_specialties(path=SPECIALTIES_PATH, families_path=FAMILIES_PATH):
    """integration/specialties.yaml as a list of its specialties in order, each with
    its `position`, or stop the build on anything in it the build can't use."""
    with open(path, encoding="utf-8") as f:
        model = yaml.safe_load(f) or {}

    def stop(why):
        raise SystemExit(f"{path}: {why}")

    extra = sorted(set(model) - {"specialties"})
    if extra:
        stop(f"has sections the build doesn't use: {extra}")
    declared = read_families(families_path)["benefits"]
    specialties, owner = [], {}
    for position, s in enumerate(model.get("specialties") or [], 1):
        key = s.get("specialty")
        if not KEY.fullmatch(str(key)):
            stop(f"specialty {key!r} isn't a lower-case key such as primary_care")
        extra = sorted(set(s) - SPECIALTY_FIELDS)
        if extra:
            stop(f"{key} has fields the build doesn't use: {extra}")
        if key in (p["specialty"] for p in specialties):
            stop(f"specialty {key} repeats")
        if not _text(s.get("name")):
            stop(f"{key} has no name")
        if s.get("benefit") not in BENEFITS:
            stop(f"{key}'s benefit is {s.get('benefit')!r}, not one of {sorted(BENEFITS)}")
        if s["benefit"] not in declared:
            stop(f"{key}'s benefit {s['benefit']} isn't one {families_path} declares")
        codes = s.get("taxonomies")
        if not isinstance(codes, list) or not codes or not all(_text(c) for c in codes):
            stop(f"{key}'s taxonomies must be a list of quoted codes")
        for code in codes:
            if code in owner:
                stop(f"taxonomy {code} is in {owner[code]} and in {key}")
            owner[code] = key
        specialties.append({"specialty": key, "name": s["name"], "benefit": s["benefit"], "position": position,
                            "taxonomies": codes})
    if not specialties:
        stop("lists no specialties")
    return specialties


class Specialties(FromYaml):
    """One row per kind of doctor, in the order the screen lists them (`position`)."""
    name = "specialties"
    key = ["specialty"]
    columns = {"specialty": "VARCHAR", "name": "VARCHAR", "benefit": "VARCHAR", "position": "INTEGER"}

    def __init__(self, path=SPECIALTIES_PATH, families_path=FAMILIES_PATH):
        super().__init__(path)
        self.families_path = families_path

    def rows(self):
        return [[s["specialty"], s["name"], s["benefit"], s["position"]]
                for s in read_specialties(self.path, self.families_path)]


class SpecialtyTaxonomies(FromYaml):
    """One row per NUCC code that counts as a kind of doctor. The build stops if
    NUCC stops listing one, so a retired code can't drop out silently."""
    name = "specialty_taxonomies"
    key = ["taxonomy_code"]
    columns = {"taxonomy_code": "VARCHAR", "specialty": "VARCHAR"}

    def __init__(self, path=SPECIALTIES_PATH, families_path=FAMILIES_PATH):
        super().__init__(path)
        self.families_path = families_path

    def rows(self):
        return [[code, s["specialty"]] for s in read_specialties(self.path, self.families_path)
                for code in s["taxonomies"]]

    def check(self, con, data_dir):
        missing = [c for (c,) in con.execute(f"""
            SELECT taxonomy_code FROM specialty_taxonomies
            WHERE taxonomy_code NOT IN (SELECT taxonomy_code FROM {NUCC_TAXONOMY.read(data_dir)})
            ORDER BY 1""").fetchall()]
        if missing:
            raise SystemExit(f"specialty_taxonomies: NUCC no longer lists {missing}; decide what replaces them")
