"""Provider ↔ procedure evidence from CMS Medicare utilization data (issue #14).

The MRF is a rate sheet — it says a code is contracted to a provider *group*,
not that any given NPI performs it. This module reads the public CMS
"by Provider and Service" extract (built by `make cms-utilization` into
data/cms/ga_provider_service.parquet) to answer "did this NPI actually bill
this code to Medicare Part B, and how much?".

Everything degrades to None / empty when the file isn't built, so the API works
before `make cms-utilization` has ever run.

Caveats (see reference/cms-utilization.md): Part B only; rows with <= 10
beneficiaries are excluded entirely; ~2-year lag; practitioner (type-1) signal.
So `billed: True` is strong evidence; `billed: False` is weak.
"""
import os

from .data_sources import CMS_UTILIZATION_PATH


def available() -> bool:
    return os.path.exists(CMS_UTILIZATION_PATH)


def did_bill(conn, npi: int, billing_code: str):
    """Medicare Part B utilization for one (npi, code), aggregated over
    place-of-service. Returns:
      None                         — CMS file not built
      {"billed": False}            — file built, no row for this pair
      {"billed": True, year, tot_srvcs, tot_benes, tot_bene_days,
       avg_mdcr_allowed, is_drug}
    tot_benes is summed across F/O rows, so it can slightly over-count a
    beneficiary seen in both settings — treat it as approximate.
    """
    if not available():
        return None
    r = conn.execute(
        f"""
        SELECT max(year),
               sum(tot_srvcs),
               sum(tot_benes),
               sum(tot_bene_day_srvcs),
               sum(avg_mdcr_alowd_amt * tot_srvcs) / nullif(sum(tot_srvcs), 0),
               bool_or(hcpcs_drug_ind = 'Y')
        FROM read_parquet('{CMS_UTILIZATION_PATH}')
        WHERE npi = ? AND hcpcs_cd = ?
        """,
        [npi, billing_code],
    ).fetchone()
    if not r or r[1] is None:
        return {"billed": False}
    return {
        "billed": True,
        "year": r[0],
        "tot_srvcs": int(r[1]),
        "tot_benes": int(r[2]) if r[2] is not None else None,
        "tot_bene_days": int(r[3]) if r[3] is not None else None,
        "avg_mdcr_allowed": round(r[4], 2) if r[4] is not None else None,
        "is_drug": bool(r[5]),
    }


def billed_codes(conn, npi: int, codes) -> dict:
    """{hcpcs_cd: {tot_srvcs, tot_benes, year}} for the subset of `codes` this
    NPI billed to Medicare. Empty dict when the file isn't built. One query —
    used to badge the provider "menu"."""
    codes = [c for c in {*codes} if c]
    if not available() or not codes:
        return {}
    placeholders = ", ".join("?" * len(codes))
    rows = conn.execute(
        f"""
        SELECT hcpcs_cd, sum(tot_srvcs), sum(tot_benes), max(year)
        FROM read_parquet('{CMS_UTILIZATION_PATH}')
        WHERE npi = ? AND hcpcs_cd IN ({placeholders})
        GROUP BY 1
        """,
        [npi, *codes],
    ).fetchall()
    return {
        r[0]: {"tot_srvcs": int(r[1]), "tot_benes": int(r[2]) if r[2] is not None else None,
               "year": r[3]}
        for r in rows
    }
