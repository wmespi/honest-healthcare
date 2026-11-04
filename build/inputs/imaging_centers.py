"""Build-only: the NPPES specialty codes for an imaging or mammography center."""
from build.inputs.datasets import NUCC_TAXONOMY
from build.table import Table


class ImagingCenters(Table):
    """Every NUCC clinic or center code whose specialization is radiology, any of
    its kinds such as mammography, or MRI, so a code NUCC adds is picked up."""
    name = "imaging_centers"
    key = ["taxonomy_code"]
    columns = {"taxonomy_code": "VARCHAR", "specialization": "VARCHAR"}

    def select(self, data_dir):
        return f"""
            SELECT taxonomy_code, specialization FROM {NUCC_TAXONOMY.read(data_dir)}
            WHERE classification = 'Clinic/Center'
              AND (specialization LIKE 'Radiology%' OR specialization = 'Magnetic Resonance Imaging (MRI)')
        """

    def check(self, con, data_dir):
        if not con.execute("SELECT count(*) FROM imaging_centers").fetchone()[0]:
            raise SystemExit("imaging_centers: no NUCC code is a radiology or MRI clinic or center, "
                             "so NUCC's names have changed; update the rule")
