# The CMS MRF data model

*Read this when you need to understand the shape of the source files — how plan,
network, file, billing code, and provider relate — or why the same rate shows up
in more than one file. Conceptual; there is no `make` target for it.*

Anthem's Machine-Readable Files are mandated by the Transparency in Coverage rule
(45 CFR § 147.210). The authoritative schema — valid `negotiated_type` values, whether
`setting` is always present, etc. — is the CMS guide:

- Schema reference: https://github.com/CMSgov/price-transparency-guide
- In-network rates: https://github.com/CMSgov/price-transparency-guide/tree/master/schemas/in-network-rates
- Table of contents: https://github.com/CMSgov/price-transparency-guide/tree/master/schemas/table-of-contents

---

## How plan, network, file, and provider relate

```
ANTHEM MASTER INDEX  (YYYY-MM-01_anthem_index.json.gz)
  │ reporting_structure[]  — one entry per PLAN
  ▼
PLAN   plan_name "BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM"
       plan_id "…"   plan_market_type "individual"
  │ in_network_files[]  — a plan links to MANY rate files
  ├───────────────────────────────┐
  ▼                               ▼
PLAN-SPECIFIC FILE            SHARED NETWORK FILE
anthem/GA_JBNKMED0001.gz      many other files, each linked
                              from many plans
  │  (both share the same internal schema)
  ▼
RATE FILE (in-network JSON)
  ├─ in_network[]              — one entry per billing code
  │    └─ negotiated_rates[]   — one entry per rate × provider-group combo
  │         ├─ provider_references: [1020000797660]   ← file-local id ref
  │         └─ negotiated_prices[]: { negotiated_rate, negotiated_type,
  │              billing_class, setting, service_code, billing_code_modifier,
  │              expiration_date }
  └─ provider_references[]     — the id → roster lookup (separate section)
       └─ { provider_group_id: 1020000797660,
            network_name: ["GA Blue Value HIX Individual Network"],
            provider_groups[]: { tin: {type,value}, npi: [1841337524, …] } }
```

Entity relationships:

```
PLAN  1─N  RATE FILE  1─N  BILLING CODE  1─N  NEGOTIATED PRICE
                                          └─  PROVIDER_REFERENCE (by id)
                                                 └─ PROVIDER GROUP (TIN)  1─N  NPI[]
```

`billing_code + billing_code_type` is the procedure key. `provider_group_id` is
**file-local** — every cross-file join keys on `(file_id, provider_group_id)`.

`provider_references` must appear before `in_network` in the byte stream for a
single pass to resolve ids → networks; `streamMRF` asserts this.

---

## Why the same rate appears in multiple files for one plan

CMS requires insurers to publish **every** rate file a plan participates in. Anthem
layers its contracts:

- **Network-level rates** live in shared files — one contract ("all GA Blue Value
  providers get fee schedule X") that thousands of plans link to.
- **Plan-level rates** live in a plan-specific file — an extra discount or custom
  tier negotiated on top of the network rate for that one plan.

So `(billing_code + provider_group + plan)` can legitimately appear in a
plan-specific file *and* one or more shared files, with different rates. The CMS
spec defines the file format but **no deduplication or precedence rule** — conflict
resolution is left entirely to the consumer.

---

## Several price lines for one provider and code

A clinician can sit under more than one price for the same billing code. The file
carries a few fields on each price line that tell the lines apart; read them before
treating a spread as a disagreement:

| Field | What it separates |
|---|---|
| `service_code` | CMS place-of-service list the line covers: office (`11`), hospital outpatient (`22`, `19`), ASC (`24`), ER (`23`), inpatient (`21`), plus others. A long list means one rate across settings. `CSTM-00` means every place of service. |
| `billing_code_modifier` | `26` professional (reading), `TC` technical (equipment), none = global |
| `billing_class` | `professional` or `institutional` claim |
| `setting` | `inpatient`, `outpatient`, or `both` |
| `negotiation_arrangement` (on the code) | `ffs`, `bundle`, `capitation` |
| `negotiated_type` | `negotiated`, `fee schedule`, `percentage`, `per diem`, `derived` |
| `expiration_date` | contract end; `9999-12-31` for none |
| `additional_information` | optional free text; the extractor does not keep it |

Two lines for one provider and code that agree on all of these have no recorded reason
to differ. Nothing in the file says which contract applies to a given patient, so a
quote that merges them shows a range and says so.

Where a price applies is a list of `provider_references`. A reference is a bucket of
many billing IDs and clinicians, not a practice, and buckets can overlap, so a practice
joins a price only through the buckets that hold it. The file is rebuilt and replaced
on every release, and its bucket layout changes with it; a build never assumes ids
carry over between releases. Re-run `make refresh` to move to the current release.

The imaging codes in the Anthem Georgia file are priced on office professional lines
only; hospital and ASC imaging is billed on institutional claims that this file does
not hold ([facility fees](../docs/known-gaps.md#imaging)).

---

## Conflict resolution strategy

*Target design, not yet enforced in code (Critical Rule 5). `build/build.py` tags
`source_kind`; the choice itself is applied at read time — [../build/build.md](../build/build.md).*

| Scenario | Resolution |
|---|---|
| Same code + provider group in a plan-specific AND a shared file | **Plan-specific wins** — the most directly negotiated rate |
| Code in a shared file but not the plan-specific file | **Include it** — members reach it through the shared network |
| Same code + provider group in two shared files | **Lower rate wins** — both are network-level |

Of the files linked to the target plan, exactly one carries its network label
(`etl/targets.yaml` `network_patterns`); the rest are shared files for other plans.
