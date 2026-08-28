#!/usr/bin/env python3
"""
Build data/reference/code_labels.parquet — a consumer-friendly label for every
billing code we've parsed, from PUBLIC data only:

  - CMS Restructured BETOS Classification System (RBCS)  -> clinical category /
    subcategory / family (185 readable families, e.g. "Arthroplasty - Knee",
    "Colonoscopy - Lesion Removal", "MRI/MRA - Spine"). Public domain.
  - the MRF's own short descriptor (`codes/*.parquet.name`) -> `short_name`,
    used as the fallback label and folded into the search text.

No AMA CPT consumer descriptors (licensed) are used or redistributed.

Output columns:
  billing_code_type | billing_code | short_name
  rbcs_category | rbcs_subcategory | rbcs_family | rbcs_is_major
  label            (rbcs_family, else a title-cased short_name)
  search_text      (lowercased: label + short_name + category words)

Usage:
  python3 -m reference.code_labels [--rbcs-url URL | --rbcs-file PATH]
                                   [--data-dir data] [--test]
"""
import argparse
import json
import re
import sys
import urllib.request

import duckdb

from ._common import fetch_to_cache, ref_dir, write_parquet_atomic

CMS_DATA_JSON = "https://data.cms.gov/data.json"
RBCS_TITLE = "Restructured BETOS Classification System"
# Fallback if data.json lookup fails (RY2026 cut; CMS re-stamps the path yearly).
RBCS_URL_FALLBACK = (
    "https://data.cms.gov/sites/default/files/2026-08/"
    "e99af22f-831c-469d-a297-2aa69d1e30fa/RBCS_Taxonomy_RY26.csv"
)


def resolve_rbcs_url() -> str:
    try:
        with urllib.request.urlopen(CMS_DATA_JSON, timeout=30) as r:
            catalog = json.load(r)
        for ds in catalog.get("dataset", []):
            if ds.get("title", "").strip() == RBCS_TITLE:
                for dist in ds.get("distribution", []):
                    url = dist.get("downloadURL") or ""
                    if dist.get("mediaType") == "text/csv" and url.endswith(".csv"):
                        return url
    except Exception as e:  # noqa: BLE001
        print(f"  (data.json lookup failed: {e} — using fallback URL)", file=sys.stderr)
    return RBCS_URL_FALLBACK


TITLE_KEEP_UPPER = {"CT", "MRI", "MRA", "ECG", "EKG", "IV", "IM", "GI", "ER", "ICU"}

# Lay search terms folded into search_text for the RBCS families patients
# actually look up. Keeps "colonoscopy", "mri back", "blood test" resolving even
# when the family name uses clinical wording.
FAMILY_SYNONYMS = {
    "Lower GI Endoscopy - Other": "colonoscopy sigmoidoscopy colon screening",
    "Lower GI Endoscopy": "colonoscopy sigmoidoscopy colon screening",
    "Colonoscopy - Lesion Removal": "colonoscopy polyp removal colon screening",
    "Upper GI Endoscopy": "endoscopy egd upper scope stomach",
    "Arthroplasty - Knee": "knee replacement total knee joint replacement",
    "Arthroplasty - Hip": "hip replacement total hip joint replacement",
    "Arthroscopy - Lower Extremity": "knee scope arthroscopy meniscus acl",
    "MRI/MRA - Spine": "mri back mri spine mri neck lumbar cervical",
    "MRI/MRA - Lower Extremity": "mri knee mri leg mri ankle mri foot",
    "MRI/MRA - Brain": "mri head mri brain",
    "CT/CTA - Head and Neck": "ct head ct scan cat scan ct neck ct sinus",
    "CT/CTA - Abdomen and Pelvis": "ct abdomen ct pelvis cat scan belly",
    "CT/CTA - Chest": "ct chest cat scan lung",
    "Mammography": "mammogram breast screening",
    "Standard X-ray": "xray x-ray radiograph",
    "X-ray - Chest": "chest xray chest x-ray",
    "Ultrasound - Abdomen and Pelvis": "ultrasound sonogram abdominal",
    "Echocardiography (TTE/TEE)": "echocardiogram echo heart ultrasound",
    "Electrocardiogram": "ecg ekg heart tracing",
    "Clinical Chemistry": "blood test blood work metabolic panel lipid cholesterol lab",
    "Blood Count": "blood test cbc blood work a1c lab",
    "Office E&M - Established": "office visit doctor visit checkup follow up",
    "Office E&M - New": "office visit new patient doctor visit",
    "Annual Wellness Visits": "annual physical wellness checkup preventive",
    "Emergency Department E&M": "er visit emergency room",
    "Psychotherapy - Nongroup": "therapy counseling mental health psychiatrist psychologist",
    "Cataract Surgery": "cataract eye surgery lens",
    "Cholecystectomy - Laparoscopic": "gallbladder removal gallbladder surgery",
    "Joint Injection": "cortisone shot joint injection steroid injection",
    "Vaccine Administration": "vaccine shot immunization flu shot",
    "PT Treatment": "physical therapy pt rehab",
}


def titlecase(s: str) -> str:
    if not s:
        return s
    out = []
    for w in re.split(r"(\s+|/|-)", s.strip()):
        if w.upper() in TITLE_KEEP_UPPER:
            out.append(w.upper())
        elif w and w[0].isalpha():
            out.append(w[:1].upper() + w[1:].lower())
        else:
            out.append(w)
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rbcs-url", default=None)
    ap.add_argument("--rbcs-file", default=None)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--test", action="store_true", help="read/write under data-test/")
    args = ap.parse_args()

    data_dir = "data-test" if args.test else args.data_dir
    codes_glob = f"{data_dir}/anthem/codes/*.parquet"
    rd = ref_dir(args.data_dir, args.test)
    rbcs_cache = f"{rd}/rbcs_taxonomy_ry26.csv"
    out_path = f"{rd}/code_labels.parquet"

    print("→ RBCS taxonomy")
    rbcs_path = fetch_to_cache(
        rbcs_cache,
        [args.rbcs_url or resolve_rbcs_url()],
        args.rbcs_file,
    )

    con = duckdb.connect()

    # One RBCS row per HCPCS code: prefer the current assignment, then the most
    # recent analysis window.
    con.execute(
        f"""
        CREATE TABLE rbcs AS
        SELECT * FROM (
          SELECT
            "HCPCS_Cd"        AS code,
            "RBCS_Cat_Desc"   AS category,
            "RBCS_Subcat_Desc" AS subcategory,
            "RBCS_Family_Desc" AS family,
            ("RBCS_Major_Ind" = 'M') AS is_major,
            row_number() OVER (
              PARTITION BY "HCPCS_Cd"
              ORDER BY "RBCS_Latest_Assignment" DESC,
                       "RBCS_Analysis_End_Dt" DESC NULLS LAST
            ) AS rn
          FROM read_csv_auto('{rbcs_path}', header=true, all_varchar=true)
        ) WHERE rn = 1
        """
    )
    n_rbcs = con.execute("SELECT count(*) FROM rbcs").fetchone()[0]
    print(f"  {n_rbcs:,} RBCS code assignments")

    print("→ distinct billing codes we've parsed")
    try:
        con.execute(
            f"""
            CREATE TABLE codes AS
            SELECT billing_code_type, billing_code,
                   arg_max(name, cnt) AS short_name
            FROM (
              SELECT billing_code_type, billing_code, name, count(*) AS cnt
              FROM read_parquet('{codes_glob}', union_by_name=true)
              GROUP BY 1, 2, 3
            )
            GROUP BY 1, 2
            """
        )
    except duckdb.IOException:
        print(f"  no code parquet at {codes_glob} — nothing to label", file=sys.stderr)
        sys.exit(1)
    n_codes = con.execute("SELECT count(*) FROM codes").fetchone()[0]
    print(f"  {n_codes:,} distinct codes")

    syn_rows = ", ".join(
        f"('{k.replace(chr(39), chr(39) * 2)}', '{v}')" for k, v in FAMILY_SYNONYMS.items()
    )
    con.execute("CREATE TABLE fam_syn(family VARCHAR, syn VARCHAR)")
    if syn_rows:
        con.execute(f"INSERT INTO fam_syn VALUES {syn_rows}")

    con.execute(
        """
        CREATE TABLE labelled AS
        SELECT
          c.billing_code_type,
          c.billing_code,
          c.short_name,
          NULLIF(NULLIF(r.category, ''), 'No RBCS Category')       AS rbcs_category,
          NULLIF(NULLIF(r.subcategory, ''), 'No RBCS Subcategory') AS rbcs_subcategory,
          NULLIF(NULLIF(r.family, ''), 'No RBCS Family')           AS rbcs_family,
          COALESCE(r.is_major, FALSE) AS rbcs_is_major,
          s.syn AS _syn
        FROM codes c
        LEFT JOIN rbcs r    ON c.billing_code = r.code
        LEFT JOIN fam_syn s ON s.family = r.family
        """
    )

    write_parquet_atomic(
        con,
        """
          SELECT
            billing_code_type,
            billing_code,
            short_name,
            rbcs_category,
            rbcs_subcategory,
            rbcs_family,
            rbcs_is_major,
            COALESCE(
              rbcs_family,
              CASE WHEN rbcs_subcategory IS NOT NULL
                   THEN concat_ws(' – ', rbcs_category, rbcs_subcategory) END,
              short_name,
              billing_code
            ) AS label,
            lower(concat_ws(' ',
              rbcs_family, rbcs_subcategory, rbcs_category,
              _syn, short_name, billing_code
            )) AS search_text
          FROM labelled
        """,
        out_path,
    )

    matched = con.execute(
        "SELECT count(*) FROM labelled WHERE rbcs_family IS NOT NULL"
    ).fetchone()[0]
    print(f"→ wrote {out_path}")
    print(f"  {matched:,}/{n_codes:,} codes have an RBCS family "
          f"({100 * matched / max(n_codes, 1):.0f}%)")
    fam = con.execute(
        """
        SELECT COALESCE(rbcs_family, '(unmapped)') f, count(*) n
        FROM labelled GROUP BY 1 ORDER BY n DESC LIMIT 12
        """
    ).fetchall()
    for f, n in fam:
        print(f"    {n:>6}  {f}")


if __name__ == "__main__":
    main()
