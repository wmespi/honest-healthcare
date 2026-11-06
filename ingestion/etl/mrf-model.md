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
THE PLAN'S CONTRACT FILE      OTHER LINKED FILES
anthem/GA_JBNKMED0001.gz      out-of-state BlueCard copies,
                              each linked from many plans
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

So `(billing_code + provider_group + plan)` can legitimately appear in several files,
with different rates. The CMS spec defines the file format but **no deduplication or
precedence rule**, so that's left to the consumer: see
[one plan, one contract](#one-plan-one-contract).

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

The imaging codes in `GA_JBNKMED0001` are priced on office professional lines only;
hospital and ASC imaging is billed on institutional claims that this file does not hold ([facility fees](../../docs/known-gaps.md#imaging)).

---

## One plan, one contract

A plan a person can pick is priced by one file, and nothing combines or overrides
prices across files ([AGENTS.md rule 5](../../AGENTS.md)). The same billing ID can carry
different prices in two files, and nothing in the files says which one applies to a
given member, so a rule that picks between them would be a guess.

Blue Value shows why the network label isn't enough. `GA_JBNKMED0001` and
`GA_JBKEMED0001` both carry `GA Blue Value HIX Individual Network`, but they serve
different plan IDs at different prices: `GA_JBKEMED0001` prices only `45334GA0010112`,
an older plan that isn't on Georgia's exchange. The rest of the files linked to the
plan are out-of-state BlueCard copies that don't carry its label. So `ingestion/etl/targets.yaml` names each plan's file, and `etl parse` stops unless the index links it to the plan IDs on the target exchange networks ([parse.md](parse.md#target-selection)).
A label never selects a file.
Parse's last step then publishes each network's rates under its target's labels, from the parsed files the index links to the network's plans, and stops unless exactly one of them carries those labels ([parse.md](parse.md#publishing-the-rates-by-network)).
