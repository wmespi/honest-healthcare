# Direction

*Where the product is going and why — the standing answer to "what are we
building toward." Thin by design: gap detail lives in
[known-gaps.md](known-gaps.md), architecture in [../AGENTS.md](../AGENTS.md).
Update this as items land; check them off in the same PR.*

---

## The point

Make navigating the US healthcare system easier for a normal person. Not a
research dataset, not an ad-supported marketplace — a straight answer to *"I need
this care; where do I get it, what will it cost, and is it any good?"* and,
increasingly, *"…now help me actually get the appointment."*

Monetization is not the driver. Keep the surface small, the data public, and the
user anonymous wherever possible.

---

## Two flows

The app answers two different questions that need two different front doors.
Today they're crammed into one filter bar — that's why the specialty control felt
like a dead end.

### Flow A — Find care  *(as-needed)*

Already on a plan → pick a procedure or a need → a ranked list **and map** of
providers/facilities, blending **price on their plan**, **quality / volume**, and
**distance**.

- Anchored on one network (theirs).
- Needs: geocoding, CMS quality (hospital + clinician), inpatient volume.
- **Closest to shippable** — rates exist; the rest is cheap public data.
- Ends in a "Request this appointment" action (see *explorer → navigator*).
- **Shipped:** the plan-first front door. The explorer is gated on plan
  selection (persisted to `localStorage`; a link bypasses it to browse all
  networks). Flow: plan → **specialty** (`/specialties`, listed alphabetically
  with provider counts shown, not ranked by them) → a ranked provider list
  (`/providers/search?specialty=`, rated providers first) → provider menu →
  procedure. `/rates/providers` + `/rates/quote` require the plan
  (`network_required`). Procedure search stays as the secondary path.
- **Next:** blend quality / volume / distance into the provider ranking — needs
  geocode + CMS quality (build sequence steps 1–2), and a `/find-care` route.

### Flow B — Pick a plan  *(open enrollment, once a year)*

Describe expected care (a few procedures / conditions / "I see Dr. Y" / "I take
drug X") → candidate individual-market plans ranked by **estimated total annual
cost**: premium + expected cost-sharing against real negotiated rates +
in-network status of their providers and drugs.

- Anchored on all GA individual-market carriers.
- Needs: multi-payer rate MRFs, Exchange plan-attribute & cost-sharing files,
  plan provider directories.
- **Further out** — blocked on the rate-store scale work
  ([#10](https://github.com/wmespi/honest-healthcare/issues/10)) — but the more
  differentiated product.

### Shared substrate

Both sit on the same foundation, which is most of the work — build it once:
procedure taxonomy (have — RBCS), provider identity + geocode (have identity,
need geocode), negotiated rates per `(payer, network, code, provider)` (have for
one payer), plan cost-sharing structure (missing), quality signal (missing).

Target: separate routes (`/find-care`, `/pick-a-plan`) over a shared component
and query layer. Ship A first.

---

## Explorer → navigator

The current app is an information tool. The direction is a **care navigator**:
the Flow A ranked list ends in *"Request this appointment."*

- **Not** a scheduling engine, **not** a HIPAA covered entity or business
  associate. We act as the consumer's assistant.
- **MVP (hands-off booking):** user picks from the ranked options → submits a
  request with the minimum needed (name, contact, procedure, plan, preferred
  window) → a human contacts the practice on their behalf and confirms back.
  Request data is used once for the hand-off, not retained as a health record.
- **Later:** aggregator APIs (Zocdoc-style partner APIs, Solv for urgent care)
  or FHIR `Slot` / `Appointment` where a system exposes them. No provider-system
  integration that would require a BAA.

---

## Staying out of HIPAA

HIPAA regulates providers, plans, clearinghouses, and their business associates —
not a tool the consumer chooses to use as their own agent. To keep it that way:

- **No accounts required.** The whole explorer works anonymously.
- **Minimum collection.** "The procedure I want" — never a diagnosis, an EOB, a
  claim, or a clinical record. No longitudinal health profile.
- **No BAA integrations.** Nothing that plugs into a provider's or plan's systems
  under contract.
- **Use-once for booking requests.** Contact + request details for a single
  hand-off, then discard.

Still applies regardless of HIPAA: a real consumer-facing privacy policy, the FTC
Health Breach Notification Rule, and state consumer-health-data laws. "Not HIPAA"
is a smaller, clearer set of obligations — not none.

---

## Other ways to make navigation easier

Low-scope, HIPAA-light features that compound on the same data:

- **Site-of-care nudge.** The same procedure at an ambulatory surgery center vs a
  hospital outpatient department is often a 2–5× price difference. We already
  have `setting` — make *"do this at an ASC and save $X"* a first-class
  recommendation.
- **Facility-fee flag.** Hospital-owned practices bill a separate facility fee;
  independent ones don't. Surface "independent" vs "hospital-owned" per provider
  (NPPES + POS + affiliation signal).
- **"Is this price fair?" check.** Paste a CPT code off a bill or a quote → see
  where it sits in the network's negotiated-rate distribution. A code, not the
  document.
- **Procedure plain-language + prep questions.** "Your doctor said you need a
  [X]" → what it is, what it usually costs, and the questions that avoid surprise
  bills ("is the anesthesiologist in-network?", "is prior auth needed?", "is the
  facility fee billed separately?").
- **"Bring this to your doctor" summary.** A shareable sheet of cheaper
  in-network options for a referral, so the patient can ask to be sent there.
- **Ghost-network check.** Cross-reference a plan's own directory JSON against
  NPPES to flag stale / wrong listings before someone drives there.
- **Rights & appeals content.** No Surprises Act, balance-billing disputes,
  itemized-bill requests — templates and explainers, no PHI.
- **Deductible timing.** From user-entered (not stored) numbers: *"you've met
  your deductible — an elective procedure now costs you only coinsurance."*

---

## Data roadmap

All free unless marked. Most fit the `reference/cms_utilization.py` pattern —
DuckDB filter/project a CMS CSV to a GA Parquet.

### For "where should I go" (Flow A)

| Source | Adds | Cost | Effort |
|---|---|---|---|
| **CMS Hospital Care Compare** (Provider Data Catalog) | star rating, mortality, readmission, safety, HCAHPS patient experience, complication & infection rates | free | low — keyed by `CCN`, needs a CCN↔NPI bridge |
| **CMS Doctors & Clinicians** (Care Compare, 7 files) | MIPS scores, procedure-of-interest volume, facility affiliations, group linkage | free | low — keyed by `NPI`, joins directly |
| **CMS Medicare Inpatient Hospitals — by Provider & Service** (MS-DRG) | per-hospital discharge volume + payment + charge — the inpatient / surgical side Part B misses | free | low — same builder shape |
| **CMS Medicare Physician & Other Practitioners — by Provider** (aggregate) | per-NPI beneficiary / service counts, patient & condition mix | free | low |
| **CMS Provider of Services (POS)** + Hospital Enrollments (PECOS) | facility attributes (beds, ownership, services) and the CCN↔NPI / address bridge | free, quarterly | low |
| Leapfrog Hospital Safety Grade | A–F hospital safety grade, recognized brand | lookup free / bulk licensed | med |
| GHA Georgia Discharge Data · HCUP SID + SASD (GA) | **all-payer** inpatient + ambulatory-surgery volume by procedure by hospital | purchase + DUA | high — revisit only if free Medicare volume is too thin |

### For "who should be my payer" (Flow B)

| Source | Adds | Cost | Effort |
|---|---|---|---|
| **Other payers' Transparency-in-Coverage MRFs** (UHC, Aetna, Cigna, Kaiser, Ambetter/Centene, CareSource, Alliant — the GA individual carriers) | rates across carriers for the same procedure/provider — makes the question answerable at all | free (mandated) | high — each carrier's index format differs; the discovery adapter is Anthem-specific ([#10](https://github.com/wmespi/honest-healthcare/issues/10) prep). Start with the 2–3 largest |
| **CMS Health Insurance Exchange PUFs** (Plan Attributes + Benefits & Cost-Sharing especially) | structured per-plan deductible, OOP max, copays, coinsurance, metal, premium, counties, network/formulary URLs | free, annual CSV | med — kills the "user types their deductible" gap |
| **QHP Landscape Files** | county plan list w/ premium, deductible, OOP, metal | free | low — GA moved to the "Georgia Access" state exchange for 2026; confirm GA still in the CMS files or pull Georgia Access's own data |
| **QHP provider & formulary machine-readable JSON** (CMS schema, ≠ TiC) | per-plan in-network NPI roster + drug formulary | free | med — directory accuracy is poor, but it's the only machine-readable roster |
| **HIOS `plan_id` ↔ issuer ↔ network crosswalk** (from the PUFs + `index_files.hios_issuer_ids`) | *derives* plan-name → network, retires the one-entry `serving/plan_networks.json` | free | med |
| CMS Marketplace Open Enrollment PUFs | enrollment by plan / metal / county | free | low — secondary |

### Geography (and the Google Maps question)

Google Maps is right for the map view and a "get directions" hand-off, wrong as a
data source: the flat $200/mo credit ended March 2025 (per-SKU free tiers now),
and Places content can't be cached beyond `place_id` — so ratings/hours can't
*live* in our data, every render would be a live billed call.

| Job | Tool | Cost |
|---|---|---|
| Geocode the provider file (one-time batch) | **US Census Geocoder** (batch, no key, 10k/batch) | free — the backbone; distance sort with no map vendor |
| "Near me" from a ZIP | Census TIGER / ZCTA centroids | free |
| Interactive map + address autocomplete | **MapLibre GL + OSM tiles**, or Mapbox (caching allowed, 50k loads/mo free) | free tier |
| Drive time / "within 20 minutes" | self-hosted OSRM / Valhalla, or OpenRouteService | free (self-host) — optional |
| Turn-by-turn | deep-link to Google / Apple Maps | free |

---

## Build sequence

Ordered so each step ships something usable and nothing waits on the multi-payer
lift longer than it must.

1. **Geocode + distance + map** — Census batch-geocode the GA NPPES subset;
   lat/long onto the Parquet; distance filter/sort + a MapLibre map. *~days, free.*
2. **CMS quality layer** — Hospital Care Compare + Doctors & Clinicians + the
   POS / Hospital Enrollments CCN↔NPI bridge. *~1–2 weeks, free.*
3. **Medicare inpatient volume (MS-DRG)** — into the evidence tiers; fills the
   surgical / admission hole Part B leaves. *~days, free.*
4. **Ship Flow A — "Find care"** — dedicated route: procedure/need → ranked list
   + map blending plan price, quality, volume, distance. *~1–2 weeks.*
5. **Exchange PUFs + HIOS crosswalk** — real per-plan cost-sharing defaults for
   the estimator; derive plan-name → network, retire `plan_networks.json`.
   *~1 week, free.*
6. **Scale the rate store** ([#10](https://github.com/wmespi/honest-healthcare/issues/10))
   — kill the fan-out, precompute the browse layer, add a `payer` dimension,
   persistent bounded DuckDB. Prerequisite for 7–8. *weeks.*
7. **Multi-payer rate ingestion** — per-carrier discovery adapters for the 2–3
   largest GA individual carriers besides Anthem, feeding the shared parse →
   Parquet → summary path. *weeks, free data.*
8. **Ship Flow B — "Pick a plan"** — expected-care input → plans ranked by
   estimated total annual cost; plan provider-directory JSON for in-network
   checks. *weeks.*

Hands-off booking (the navigator MVP) slots in after step 4 — it's a request form
and an ops process, not an integration.
