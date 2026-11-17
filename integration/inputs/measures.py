"""Build-only: the quality measures in integration/measures.yaml, which say what the
screen shows beside a place or a clinician and which services each one fits:
the measures (`measures`) and the families or services each fits (`measure_fits`)."""
import os

import yaml

from integration.inputs.datasets import CLINICIAN_VOLUME
from integration.inputs.families import FAMILIES_PATH, KEY, FromYaml, _text, read_families

MEASURES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "measures.yaml")
MEASURE_FIELDS = {"measure", "name", "about", "subject", "source", "unit", "category", "fits"}
FIT_FIELDS = {"family", "services"}
SUBJECTS = {"place", "clinician"}
UNITS = {"percent", "per_1000", "ratio", "stars", "procedures"}
# Each source's dataset, as the screen labels it, and the subject it measures.
SOURCES = {"hospital-measures": ("CMS hospital outpatient measures", "place"),
           "asc-measures": ("CMS surgery center measures", "place"),
           "clinician-volume": ("CMS Doctors and Clinicians", "clinician")}


def read_measures(path=MEASURES_PATH, families_path=FAMILIES_PATH):
    """integration/measures.yaml as a list of its measures in order, each with its
    `position`, or stop the build on anything in it the build can't use."""
    with open(path, encoding="utf-8") as f:
        model = yaml.safe_load(f) or {}

    def stop(why):
        raise SystemExit(f"{path}: {why}")

    extra = sorted(set(model) - {"measures"})
    if extra:
        stop(f"has sections the build doesn't use: {extra}")
    declared = {f["family"] for f in read_families(families_path)["families"]}
    measures = []
    for position, m in enumerate(model.get("measures") or [], 1):
        key = m.get("measure")
        if not _text(key) or not KEY.fullmatch(str(key).lower().replace("-", "_")):
            stop(f"measure {key!r} isn't an id such as OP-32 or volume-colonoscopy")
        if key in (x["measure"] for x in measures):
            stop(f"measure {key} repeats")
        extra = sorted(set(m) - MEASURE_FIELDS)
        if extra:
            stop(f"{key} has fields the build doesn't use: {extra}")
        for field in ("name", "about"):
            if not _text(m.get(field)):
                stop(f"{key} has no {field}")
        if m.get("source") not in SOURCES:
            stop(f"{key}'s source is {m.get('source')!r}, not one of {sorted(SOURCES)}")
        if m.get("subject") not in SUBJECTS:
            stop(f"{key}'s subject is {m.get('subject')!r}, not one of {sorted(SUBJECTS)}")
        if SOURCES[m["source"]][1] != m["subject"]:
            stop(f"{key}'s source {m['source']} measures a {SOURCES[m['source']][1]}, not a {m['subject']}")
        if m.get("unit") not in UNITS:
            stop(f"{key}'s unit is {m.get('unit')!r}, not one of {sorted(UNITS)}")
        if (m["source"] == "clinician-volume") != _text(m.get("category")):
            stop(f"{key} needs a category exactly when its source is clinician-volume")
        fits = m.get("fits")
        if not isinstance(fits, list):
            stop(f"{key}'s fits must be a list, empty when it fits no family yet")
        seen = set()
        for fit in fits:
            if not isinstance(fit, dict) or set(fit) - FIT_FIELDS or "family" not in fit:
                stop(f"{key}'s fits must each be a family, optionally with services")
            if fit["family"] not in declared:
                stop(f"{key} fits family {fit['family']}, which {families_path} doesn't declare")
            if fit["family"] in seen:
                stop(f"{key} fits family {fit['family']} twice")
            seen.add(fit["family"])
            services = fit.get("services", [])
            if not isinstance(services, list) or not all(_text(s) for s in services) \
                    or ("services" in fit and not services):
                stop(f"{key}'s services for {fit['family']} must be a list of service ids")
        measures.append({**m, "position": position, "category": m.get("category"),
                         "fits": [{"family": f["family"], "services": f.get("services", [])} for f in fits]})
    if not measures:
        stop("lists no measures")
    return measures


class Measures(FromYaml):
    """One row per measure, in the order the screen lists them (`position`)."""
    name = "measures"
    key = ["measure"]
    columns = {"measure": "VARCHAR", "name": "VARCHAR", "about": "VARCHAR", "subject": "VARCHAR",
               "source": "VARCHAR", "unit": "VARCHAR", "category": "VARCHAR", "position": "INTEGER"}

    def __init__(self, path=MEASURES_PATH, families_path=FAMILIES_PATH):
        super().__init__(path)
        self.families_path = families_path

    def rows(self):
        return [[m[c] for c in self.columns] for m in read_measures(self.path, self.families_path)]

    def check(self, con, data_dir):
        """Stop on a volume category CMS's file doesn't have, so a renamed one can't
        drop a measure silently."""
        missing = [c for (c,) in con.execute(f"""
            SELECT category FROM measures
            WHERE category IS NOT NULL AND category NOT IN (SELECT category FROM {CLINICIAN_VOLUME.read(data_dir)})
            ORDER BY 1""").fetchall()]
        if missing:
            raise SystemExit(f"measures: clinician volume has no category {missing}; see "
                             f"ingestion/reference/clinician-volume.md for the list")


class MeasureFits(FromYaml):
    """One row per measure and family it fits, with the `services` it is narrowed
    to, empty for the whole family."""
    name = "measure_fits"
    key = ["measure", "family"]
    columns = {"measure": "VARCHAR", "family": "VARCHAR", "services": "VARCHAR[]"}

    def __init__(self, path=MEASURES_PATH, families_path=FAMILIES_PATH):
        super().__init__(path)
        self.families_path = families_path

    def rows(self):
        return [[m["measure"], f["family"], f["services"]]
                for m in read_measures(self.path, self.families_path) for f in m["fits"]]

    def check(self, con, data_dir):
        """Stop on a service a measure names that isn't in its family."""
        stray = con.execute("""
            SELECT f.measure, s, f.family FROM (SELECT *, unnest(services) AS s FROM measure_fits) f
            WHERE (f.family, s) NOT IN (SELECT (family, service_id) FROM service_list)
            ORDER BY 1, 2""").fetchone()
        if stray:
            raise SystemExit(f"measure_fits: {stray[0]} fits service {stray[1]}, which isn't a service in {stray[2]}")


# Each measure and the services it fits (`measure`, `service_id`): the whole family
# unless the fit names services.
FITS = """
    SELECT f.measure, s.service_id
    FROM measure_fits f JOIN service_list s ON s.family = f.family
    WHERE len(f.services) = 0 OR list_contains(f.services, s.service_id)
"""
