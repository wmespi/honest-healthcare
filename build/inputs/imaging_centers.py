"""Build-only: the NPPES specialty codes for an imaging or mammography center."""
from build.inputs.datasets import NUCC_TAXONOMY
from build.table import Table

# Which centers count is a product decision (#135, 2026-11-04), so it's a list
# of NUCC's stable codes, not a match on its labels, which NUCC edits.
CODES = ("261QM1200X", "261QR0200X", "261QR0206X", "261QR0207X", "261QR0208X")


class ImagingCenters(Table):
    """The MRI, radiology, mammography, mobile mammography and mobile radiology
    clinic or center codes, each as NUCC lists it. The build stops if NUCC stops
    listing one, so a retired code can't drop out silently."""
    name = "imaging_centers"
    key = ["taxonomy_code"]
    columns = {"taxonomy_code": "VARCHAR", "specialization": "VARCHAR"}

    def select(self, data_dir):
        codes = ", ".join(f"'{c}'" for c in CODES)
        return f"""
            SELECT taxonomy_code, specialization FROM {NUCC_TAXONOMY.read(data_dir)}
            WHERE taxonomy_code IN ({codes})
        """

    def check(self, con, data_dir):
        listed = {r[0] for r in con.execute("SELECT taxonomy_code FROM imaging_centers").fetchall()}
        missing = sorted(set(CODES) - listed)
        if missing:
            raise SystemExit(f"imaging_centers: NUCC no longer lists {missing}; decide what replaces them")
