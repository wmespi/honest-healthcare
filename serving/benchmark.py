"""Medicare hospital-outpatient (OPPS) benchmark, see reference/opps.md.

The build puts the national payment per code on `code_dim.opps_payment`.
"""
from .data_sources import CODE_DIM_SRC, built_with


def opps_payment(conn, billing_code: str, billing_code_type: str = "CPT"):
    """National Medicare hospital-outpatient (OPPS) payment $ for one code — the
    hospital's side of a scan done in a hospital — or None when OPPS wasn't a
    build input or the code carries no OPPS rate."""
    if not built_with("opps"):
        return None
    row = conn.execute(f"""
        SELECT opps_payment FROM {CODE_DIM_SRC}
        WHERE billing_code = ? AND billing_code_type = ? AND opps_payment IS NOT NULL
        LIMIT 1
    """, [billing_code, billing_code_type]).fetchone()
    return round(row[0], 2) if row and row[0] is not None else None
