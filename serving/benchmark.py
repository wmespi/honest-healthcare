"""Medicare Physician Fee Schedule benchmark (issue #61).

The MRF prices provider *groups*, not providers, so most provider drill-downs
have no per-provider rate. The Medicare allowed amount is a per-code reference
point: a sanity check on a group rate ("$180 vs Medicare's $60 = plausible" vs
the $4.4M drug-code garbage — #51) and a fallback price when the MRF is
group-rate noise.

`medicare_allowed(conn, billing_code, billing_code_type, modifier, pos)` reads
`data/reference/mpfs_ga.parquet` (`make mpfs`) and returns the Georgia allowed
amount, or `None` when the file isn't built / the code isn't on the physician
fee schedule (bundled / not covered / carrier-priced / facility-only) / no GA
locality row matches.

Georgia has two GPCI localities (`01` Atlanta metro, `99` rest of state); this
returns the median across whichever localities the file carries — one GA number.

Physician fee schedule only. Facility fees (hospital-outpatient OPPS, ASC,
inpatient IPPS) are separate schedules — see reference/mpfs.md.
"""
import os

from .data_sources import MPFS_GA_PATH


def available() -> bool:
    return os.path.exists(MPFS_GA_PATH)


def medicare_allowed(conn, billing_code: str, billing_code_type: str = "CPT",
                     modifier: str = "", pos: str = "nonfacility"):
    """GA Medicare allowed $ for one (code, modifier, POS), or None.

    `pos` is matched loosely: anything starting "f" → facility, else
    non-facility (the office / freestanding setting a shoppable rate compares
    against). `billing_code_type` is matched when given; pass "" to ignore it
    (the MPFS file types 5-digit codes 'CPT' and the rest 'HCPCS')."""
    if not available():
        return None
    pos = "facility" if str(pos).lower().startswith("f") else "nonfacility"
    type_clause = "AND billing_code_type = ?" if billing_code_type else ""
    params = [billing_code]
    if billing_code_type:
        params.append(billing_code_type)
    params += [modifier or "", pos]
    row = conn.execute(
        f"""
        SELECT median(medicare_allowed)
        FROM read_parquet('{MPFS_GA_PATH}')
        WHERE billing_code = ? {type_clause}
          AND COALESCE(modifier, '') = ?
          AND pos = ?
          AND medicare_allowed IS NOT NULL
        """,
        params,
    ).fetchone()
    return round(row[0], 2) if row and row[0] is not None else None
