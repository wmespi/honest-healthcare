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
anthem/GA_JBNKMED0001.gz      ~244 other files, up to 144k plans each
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

## Conflict resolution strategy

*Target design — documented here, not yet enforced in code (Critical Rule 5).*

| Scenario | Resolution |
|---|---|
| Same code + provider group in a plan-specific AND a shared file | **Plan-specific wins** — the most directly negotiated rate |
| Code in a shared file but not the plan-specific file | **Include it** — members reach it through the shared network |
| Same code + provider group in two shared files | **Lower rate wins** — both are network-level |

Implementation needs `source_file_id` and `plan_count` (plans the file serves) on
every price row; queries then rank single-plan rows above multi-plan rows on tie-break.
