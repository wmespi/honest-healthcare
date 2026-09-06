"""Provider ↔ procedure evidence (issue #14) — read off the build's `evidence`
table.

The MRF is a rate sheet — it says a code is contracted to a provider *group*,
not that any given NPI performs it. The build (build/build.py) joins the public
CMS "by Provider and Service" extract and the per-specialty procedure
profiles into one `(npi, billing_code, tier)` table for every NPI reachable
through a rate:

  billed   — this NPI billed the code to Medicare Part B; the row carries the
             utilization detail (year, tot_srvcs, tot_benes, tot_bene_days,
             avg_mdcr_allowed, is_drug)
  typical  — >= TYPICAL_THRESHOLD (3%, the build floor) of the NPI's NUCC
             classification bill it; the row carries that `prevalence`, so a
             request can tighten the threshold (never loosen it)

Anything without a row is tier 3, "group" — the rate reaches the provider only
through a shared billing group. `available()` is whether the CMS extract was an
input to the build (data_sources.built_with); when it wasn't, did_bill() is None
and every code is "group", same as before the build existed.

Caveats (see reference/cms-utilization.md): Part B only; rows with <= 10
beneficiaries are excluded entirely; ~2-year lag; practitioner (type-1) signal.
So `billed: True` is strong evidence; `billed: False` is weak.
"""
from .data_sources import EVIDENCE_SRC, PROVIDER_DIM_SRC, built_with

# The build's "typical" floor (build/build.py TYPICAL_THRESHOLD). Tunable
# upward per request via ?typical_threshold; a lower value has no extra rows to
# reveal.
DEFAULT_TYPICAL_THRESHOLD = 0.03


def available() -> bool:
    return built_with("cms_utilization")


def profiles_available() -> bool:
    return built_with("specialty_profiles")


def did_bill(conn, npi: int, billing_code: str):
    """Medicare Part B utilization for one (npi, code). Returns:
      None                         — CMS extract wasn't a build input
      {"billed": False}            — no row for this pair
      {"billed": True, year, tot_srvcs, tot_benes, tot_bene_days,
       avg_mdcr_allowed, is_drug}
    tot_benes is summed across F/O rows, so it can slightly over-count a
    beneficiary seen in both settings — treat it as approximate.
    """
    if not available():
        return None
    r = conn.execute(f"""
        SELECT year, tot_srvcs, tot_benes, tot_bene_days, avg_mdcr_allowed, is_drug
        FROM {EVIDENCE_SRC}
        WHERE npi = ? AND billing_code = ? AND tier = 'billed'
        LIMIT 1
    """, [npi, billing_code]).fetchone()
    if not r:
        return {"billed": False}
    return {
        "billed": True,
        "year": r[0],
        "tot_srvcs": int(r[1]) if r[1] is not None else None,
        "tot_benes": int(r[2]) if r[2] is not None else None,
        "tot_bene_days": int(r[3]) if r[3] is not None else None,
        "avg_mdcr_allowed": r[4],
        "is_drug": bool(r[5]),
    }


def medicare_specialty(conn, npi: int):
    """CMS's rendering-provider specialty label for this NPI
    (`provider_dim.cms_provider_type`), or None if the CMS extract wasn't built
    / the NPI has no Part B claims. Cleaner than a vague NUCC taxonomy — folded
    into plausibility()."""
    if not available():
        return None
    r = conn.execute(
        f"SELECT cms_provider_type FROM {PROVIDER_DIM_SRC} WHERE npi = ? LIMIT 1", [npi]
    ).fetchone()
    return r[0] if r else None


def typical_codes(conn, npi: int, threshold: float = DEFAULT_TYPICAL_THRESHOLD) -> set:
    """HCPCS codes typical for this NPI's NUCC classification at >= `threshold`
    prevalence (Tier 2). Empty when the profiles weren't built or the
    classification was too small to be profiled."""
    rows = conn.execute(f"""
        SELECT billing_code FROM {EVIDENCE_SRC}
        WHERE npi = ? AND tier = 'typical' AND prevalence >= ?
    """, [npi, threshold]).fetchall()
    return {r[0] for r in rows}


def all_billed_codes(conn, npi: int) -> set:
    """Every HCPCS code this NPI billed to Medicare (any code, not menu-scoped)."""
    rows = conn.execute(
        f"SELECT billing_code FROM {EVIDENCE_SRC} WHERE npi = ? AND tier = 'billed'", [npi]
    ).fetchall()
    return {r[0] for r in rows}


def billed_codes(conn, npi: int, codes) -> dict:
    """{hcpcs_cd: {tot_srvcs, tot_benes, year}} for the subset of `codes` this
    NPI billed to Medicare. One query — used to badge the provider "menu"."""
    codes = [c for c in {*codes} if c]
    if not codes:
        return {}
    placeholders = ", ".join("?" * len(codes))
    rows = conn.execute(f"""
        SELECT billing_code, tot_srvcs, tot_benes, year
        FROM {EVIDENCE_SRC}
        WHERE npi = ? AND tier = 'billed' AND billing_code IN ({placeholders})
    """, [npi, *codes]).fetchall()
    return {
        r[0]: {"tot_srvcs": int(r[1]) if r[1] is not None else None,
               "tot_benes": int(r[2]) if r[2] is not None else None,
               "year": r[3]}
        for r in rows
    }


def code_tiers(conn, npi: int, codes, threshold: float = DEFAULT_TYPICAL_THRESHOLD) -> dict:
    """{code: "billed" | "typical" | "group"} for each of `codes`.
      billed  — this NPI billed it to Medicare (Tier 1, strong)
      typical — billed by >= threshold of the NPI's classification (Tier 2)
      group   — neither; the rate reaches this provider only through a shared
                billing group (Tier 3, fan-out noise)"""
    billed = all_billed_codes(conn, npi)
    typical = typical_codes(conn, npi, threshold)
    return {c: "billed" if c in billed else ("typical" if c in typical else "group")
            for c in codes}
