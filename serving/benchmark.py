"""Medicare Physician Fee Schedule benchmark (issue #61).

The MRF prices provider *groups*, not providers, so most provider drill-downs
have no per-provider rate. The Medicare allowed amount is a per-code reference
point: a sanity check on a group rate ("$180 vs Medicare's $60 = plausible" vs
the $4.4M drug-code garbage — #51) and a fallback price when the MRF is
group-rate noise.

The build (build/build.py) reduces `data/reference/mpfs_ga.parquet` (`make
mpfs`) to one Georgia number per code — the median non-facility allowed amount
across the two GA GPCI localities, global modifier — on `code_dim.medicare_allowed`
(and, per modifier, on every `rates` row). `medicare_allowed()` is that lookup;
`None` when MPFS wasn't a build input or the code isn't on the physician fee
schedule (bundled / not covered / carrier-priced / facility-only).

Physician fee schedule only. Facility fees are separate schedules; the
hospital-outpatient (OPPS) rate is `opps_payment()` below — see reference/opps.md.
"""
from .data_sources import CODE_DIM_SRC, built_with


def available() -> bool:
    return built_with("mpfs")


def medicare_allowed(conn, billing_code: str, billing_code_type: str = "CPT"):
    """GA Medicare allowed $ for one code (global modifier, non-facility — the
    office / freestanding setting a shoppable rate compares against), or None."""
    if not available():
        return None
    row = conn.execute(f"""
        SELECT medicare_allowed FROM {CODE_DIM_SRC}
        WHERE billing_code = ? AND billing_code_type = ? AND medicare_allowed IS NOT NULL
        LIMIT 1
    """, [billing_code, billing_code_type]).fetchone()
    return round(row[0], 2) if row and row[0] is not None else None


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
