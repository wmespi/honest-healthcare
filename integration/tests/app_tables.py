"""The app's test tables: the build's default test corpus (corpus.py), built by
`make build`'s own build() into data-test/serving/, which the app's tests read
(application/serving/tests/conftest.py). `make test` runs this before them, so the app's
tests read tables integration wrote, handed over the way the real ones are.

  python -m integration.tests.app_tables
"""
import os
import shutil
import tempfile

from integration.build import build
from integration.tests.corpus import BLUE_VALUE, PATHWAY, default_corpus, office_visit_practice

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(REPO, "data-test", "serving")


def main():
    os.makedirs(OUT, exist_ok=True)
    # Empty the folder rather than remove it: in the dev stack it's a mount.
    for name in os.listdir(OUT):
        path = os.path.join(OUT, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    os.environ.pop("DUCKDB_TMP", None)
    with tempfile.TemporaryDirectory() as tmp:
        c = default_corpus(f"{tmp}/corpus")
        office_visit_practice(c)
        c.add_stays()
        # Grady is paid a day's rate for a vaginal delivery on Pathway, whose file prices the doctor's
        # fee on both networks, and Blue Value's file prices no hospital's stay at all.
        c.facility_price(PATHWAY, 50, "807", 2472, setting="inpatient", code_type="MS-DRG",
                         negotiated_type="per diem")
        for network in (PATHWAY, BLUE_VALUE):
            c.extra_price(network, "59400", 1800, setting="both")
        c.flush()
        rows = build(c.root, OUT, c.services_file, c.hospital_plans_file, c.families_file,
                     c.specialties_file, c.measures_file)
    print(f"→ the app's test tables in {OUT}: {rows}")


if __name__ == "__main__":
    main()
