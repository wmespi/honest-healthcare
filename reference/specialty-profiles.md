# `make reference STEP=specialty-profiles` — what each specialty typically bills

*Read this alongside [cms-utilization.md](cms-utilization.md) — it is Tier 2 of the
provider↔procedure story ([#14](https://github.com/wmespi/honest-healthcare/issues/14)).*

**Builds** `data/reference/specialty_procedure_profiles.parquet`: per provider
specialty, the procedures a meaningful share of that specialty performs, learned from
CMS Medicare utilization. Module `reference/specialty_profiles.py`. **Depends on**
`make reference STEP=cms-utilization`, `STEP=nppes` and `STEP=taxonomy-labels` having
run — a pure relational reshape over those landed Parquets, no download.

**Why.** Tier 1 (`did_bill`) is strong but narrow: only providers with a Medicare
footprint have it, and it misses commercial-only work. Tier 2 generalises: a code
billed by ≥ *threshold* of a provider's specialty is plausible for them without a
direct utilization row.

## How

Join CMS by-provider-and-service → NPPES (by NPI) → NUCC taxonomy, tagging every billed
`(NPI, HCPCS)` with the provider's NUCC **classification**. Per classification:

```
prevalence = (distinct NPIs of that classification billing the code)
           / (distinct NPIs of that classification with any Medicare claim)
```

Specialties with < 20 Medicare providers are dropped; every code with prevalence
≥ 0.005 is stored and serving thresholds at query time
(`DEFAULT_TYPICAL_THRESHOLD = 0.03` in `serving/evidence.py`, tunable via
`?typical_threshold`).

## Output

```
specialty            NUCC classification
hcpcs_cd
billers              distinct NPIs of this specialty that billed the code
specialty_providers  distinct NPIs of this specialty with any Medicare claim
prevalence           billers / specialty_providers  (0..1)
```

Read by `serving/evidence.py:typical_codes()` / `code_tiers()`.

## Caveats

- **Medicare-derived**: specialties that barely see Medicare (pediatrics, OB) have
  thinner profiles.
- Vague NUCC classifications ("Specialist", "Clinic/Center") give broad, noisy
  profiles — acceptable for a hint.
- "Typical for the specialty" ≠ "this provider does it"; softer than Tier 1.

Tested by `serving/tests/test_specialty_profiles.py` (prevalence math, min-provider
guard) on tiny CMS / NPPES / NUCC Parquets.
