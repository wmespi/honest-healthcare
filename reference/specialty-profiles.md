# `make specialty-profiles` — what each specialty typically bills

*Read this alongside [cms-utilization.md](cms-utilization.md) — it's Tier 2 of the
provider↔procedure story (issue #14).*

Builds `data/reference/specialty_procedure_profiles.parquet`: for each provider
specialty, the procedures a meaningful share of that specialty actually performs,
learned from CMS Medicare utilization.

`make specialty-profiles` → `python3 -m reference.specialty_profiles
--data-dir /app/data` in the serving container (`reference/specialty_profiles.py`).
**Depends on** `make cms-utilization`, `make nppes`, and `make taxonomy-labels`
having run — it's a pure relational reshape over those three landed Parquets, no
download.

## Why

Tier 1 (`did_bill`) is strong but narrow: only ~43% of the providers we can price
have any Medicare footprint, and it misses commercial-only work. Tier 2
generalises — if a code is billed by ≥ *threshold* of a provider's specialty,
it's plausible for them even without a direct utilization row. Measured
retention: strict Tier 1 keeps ~47% of priceable providers; Tier 1+2 keeps ~94%,
and only ~8% of providers are in a specialty too thin to profile (mostly
chiropractors, whose real code set is tiny anyway).

## How

Join CMS by-provider-and-service → NPPES (by NPI) → NUCC taxonomy, so every
billed `(NPI, HCPCS)` is tagged with the provider's NUCC **classification**
("Cardiology", "Pediatrics", …). Then per classification:

```
prevalence = (distinct NPIs of that classification billing the code)
           / (distinct NPIs of that classification with any Medicare claim)
```

- Specialties with < 20 Medicare providers are dropped (too small to profile).
- All codes with prevalence ≥ 0.005 are stored; the serving layer thresholds at
  query time (`DEFAULT_TYPICAL_THRESHOLD = 0.03`, tunable via
  `?typical_threshold`).

## Output — `data/reference/specialty_procedure_profiles.parquet`

```
specialty            NUCC classification
hcpcs_cd
billers              distinct NPIs of this specialty that billed the code
specialty_providers  distinct NPIs of this specialty with any Medicare claim
prevalence           billers / specialty_providers  (0..1)
```

~5.8k `(specialty, code)` rules across ~51 specialties (2024). Read by
`serving/evidence.py:typical_codes()` / `code_tiers()`.

## Caveats

- **Medicare-derived**, so specialties that barely see Medicare (pediatrics, OB)
  have thinner profiles — still usable (peds ~90 codes, OB ~25) but less complete.
- The NUCC classification can be vague ("Specialist", "Clinic/Center") — those
  buckets produce broad, noisy profiles. Acceptable for a Tier-2 hint.
- "Typical for the specialty" ≠ "this provider does it" — it's softer than Tier 1.

## Tests

`serving/tests/test_specialty_profiles.py` — hermetic, writes tiny CMS / NPPES /
NUCC Parquets under `data-test/`, runs the builder, checks the prevalence math
and the min-provider guard. Picked up by `make test-api`.
