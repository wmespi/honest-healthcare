"""Build-only: the NPPES specialty codes for an imaging or mammography center,
and which scans each one does."""
from integration.inputs.datasets import NUCC_TAXONOMY
from integration.table import Table

# Which centers count, and for which scans, is a product decision (#135,
# 2026-11-04), so it's NUCC's stable codes, not a match on its labels, which NUCC
# edits. A scan's kind is the first part of its service_id (integration/services.yaml),
# and None means every scan.
CODES = {
    "261QM1200X": "mri",        # MRI center
    "261QR0200X": None,         # radiology center
    "261QR0206X": "mammogram",  # mammography center
    "261QR0207X": "mammogram",  # mobile mammography
    "261QR0208X": None,         # mobile radiology
}


class ImagingCenters(Table):
    """Each code as NUCC lists it, with the kind of scan a center registered under
    it does (`scans`, empty for every kind). The build stops if NUCC stops listing
    one, so a retired code can't drop out silently."""
    name = "imaging_centers"
    key = ["taxonomy_code"]
    columns = {"taxonomy_code": "VARCHAR", "specialization": "VARCHAR", "scans": "VARCHAR"}

    def select(self, data_dir):
        rows = ", ".join(f"('{c}', {'NULL' if k is None else repr(k)})" for c, k in CODES.items())
        return f"""
            SELECT n.taxonomy_code, n.specialization, c.scans
            FROM {NUCC_TAXONOMY.read(data_dir)} n
            JOIN (VALUES {rows}) AS c(taxonomy_code, scans) USING (taxonomy_code)
        """

    def check(self, con, data_dir):
        listed = {r[0] for r in con.execute("SELECT taxonomy_code FROM imaging_centers").fetchall()}
        missing = sorted(set(CODES) - listed)
        if missing:
            raise SystemExit(f"imaging_centers: NUCC no longer lists {missing}; decide what replaces them")
