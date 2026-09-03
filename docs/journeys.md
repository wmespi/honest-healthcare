# User journeys

*The canonical set of "a real person does a real thing" walks through the rate
explorer. Each one is a persona + a plan + a clickpath + an expected outcome.*

**Why this file exists.** When agents write most of the diff, nobody is holding
the whole product in their head. A journey is the unit that keeps it honest: it's
the sanity check a PR author walks before merge (`Journeys touched:` in the PR
body), it's what `make journeys` asserts against the live API, and the status
table below is the "how are the full flows doing" dashboard.

`make journeys` runs the **API-level** assertions (needs the real corpus — local
only, not CI) and reports **latency** per journey: total wall time, call count,
and the single slowest call (flagged `⚠` over ~1.5s — informational, not a
failure). Browser-level Playwright specs that walk the actual clickpath are a
follow-up ([#72](https://github.com/wmespi/honest-healthcare/issues/72)).

Keep the status column honest — it's what the API returned *today*, not what a PR
claimed. If a journey regresses, that's the headline of the next
[State of the Build](../.claude/skills/state-of-the-build/SKILL.md), not a
footnote.

---

## Personas

| Name | Who | Plan | Network filter |
|---|---|---|---|
| **Rosa** | 63, near-retirement, on an individual HMO in Georgia — the primary use case ([project charter](../AGENTS.md)) | `BLUE VALUE IND NETWORK HMO - INDIV - ANTHEM` | `GA Blue Value HIX Individual Network` |
| **Dana** | Comparison shopper with no plan yet, "just looking" | — | none (browses all networks) |

---

## Flow A — "find care" (shipped)

### J1 — Rosa: what will a routine check-up cost?

| | |
|---|---|
| **Plan** | GA Blue Value HIX Individual Network |
| **Clickpath** | land → plan gate (pick plan) → "Family Medicine" → ranked provider list → pick a provider → provider menu → `99213` (office visit, established) |
| **API calls** | `/specialties?network_name=` · `/providers/search?specialty=Family Medicine&network_name=` · `/providers/{npi}/procedures?network_name=` · `/rates/quote?billing_code=99213&npi={npi}&network_name=` |
| **Expected** | One clear dollar figure, a Medicare benchmark line, and a "you'd pay ≈" once she's entered her cost-sharing. |
| **Status** | ⚠️ **works with rough edges.** Quote returns `$82.05`, `medicare_allowed $86.75`, `vs_medicare 0.95`, `tier billed`. Two problems: (a) `99213` comes back `basis: component`, and the frontend only renders the Medicare line when `basis === 'global'` — so the flagship number shows *without* its benchmark; (b) the first "Family Medicine" search result is a physical-therapy group (specialty-classification bleed). Both tracked in [#73](https://github.com/wmespi/honest-healthcare/issues/73). |

### J2 — Rosa: a knee MRI

| | |
|---|---|
| **Plan** | GA Blue Value HIX Individual Network |
| **Clickpath** | plan set → procedure search "MRI knee" / `73721` → rate distribution → provider compare, or drill to one provider's cost card |
| **API calls** | `/billing_codes?q=` · `/rates/distribution?billing_code=73721&network_name=` · `/rates/providers?billing_code=73721&network_name=` · `/rates/quote?billing_code=73721&npi={npi}&network_name=` |
| **Expected** | The rate range, the professional-fee / technical-fee split explained, the by-setting spread. |
| **Status** | ⚠️ **works, benchmark is suspect.** At the J1 provider: `$443` global, `vs_medicare 2.31`, `tier group`. The 2.3× amber flag fires — but this is a group-fanout rate reaching a family-medicine NPI through a shared billing group of ~11k codes. The group-rate disclosure does show, which softens it. Benchmarking fan-out against Medicare is the open question ([#73](https://github.com/wmespi/honest-healthcare/issues/73), [project_cms_data_pull](../AGENTS.md)). |

### J3 — Rosa: is my doctor in this plan?

| | |
|---|---|
| **Plan** | GA Blue Value HIX Individual Network |
| **Clickpath** | plan set → provider search by name → read the "has rates" / "not in Blue Value" badge (no drill required) |
| **API calls** | `/providers/search?q={name}&network_name=` |
| **Expected** | Answerable without hitting a dead-end quote screen. A provider with no rate in the plan renders as an inert listing, not a broken link. |
| **Status** | ✅ **works.** `search?q=ABBASI` returns providers with `has_rates: true/false` per NPI; the frontend renders the `false` rows disabled with a "not in {plan}" label (shipped in #57 follow-ups). |

### J5 — Rosa: a screening colonoscopy

| | |
|---|---|
| **Plan** | GA Blue Value HIX Individual Network |
| **Clickpath** | plan set → "Gastroenterology" → provider → `45378` (diagnostic colonoscopy) |
| **API calls** | `/providers/search?specialty=Gastroenterology&network_name=` · `/rates/quote?billing_code=45378&npi={npi}&network_name=` |
| **Expected** | The rate, the Medicare benchmark, and an honest "this is the group's rate, not verified to this provider" caveat when that's the case. |
| **Status** | ⚠️ **works; the plan-compare below it is noise *and* slow.** Quote: `$214.67–$290.95` global, `vs_medicare 0.68`, `tier group`. The "Does your plan matter?" card lists **54 networks** — every Anthem partition, most irrelevant to a GA individual HMO member — on a page where Rosa already picked her plan, and the `/rates/by_network` call behind it runs **~10s** on this corpus. Both point at [#73](https://github.com/wmespi/honest-healthcare/issues/73) (demote the plan-compare). |

---

## Browse — no plan

### J4 — Dana: just looking, no plan

| | |
|---|---|
| **Plan** | none |
| **Clickpath** | land → "explore all networks without picking a plan" → network overview |
| **API calls** | `GET /` · `/rates/distribution` (no `network_name`) |
| **Expected** | No misleading aggregate presented as a finding; a clear signal that a plan is needed for real numbers. |
| **Status** | ⚠️ **technically works, the numbers mislead.** The trust bar *does* warn ("All Networks mixes GA Blue Value with national mirror data"). But the page still renders a 4-stat grid — `median $425, avg $890, max $5,000+` — and a histogram, computed over **497 million rate entries across every code and every network**. That's a number nobody can act on, shown with the visual weight of an answer ([#73](https://github.com/wmespi/honest-healthcare/issues/73): retire the overview histogram). |

---

## Flow B — "pick a plan" (not built — blocked on [#10](https://github.com/wmespi/honest-healthcare/issues/10))

Listed so they're not forgotten. Each needs multi-payer rate MRFs + the CMS
Exchange cost-sharing files.

- **J6** — Dana: "I expect a knee replacement and two specialist visits next year — which individual plan costs me the least all-in?"
- **J7** — Dana: "I take drug X — which plans cover it, and at what tier?"
- **J8** — Rosa: "are all three of my current doctors in-network on this plan?"

---

## Adding a journey

1. Add the row here (persona, plan, clickpath, API calls, expected, status).
2. Add its assertion to `scripts/journeys.py` — pointed checks on the *specific*
   expected outcome (exact rate, benchmark band, tier), not a broad basket
   (`make smoke-web` already does breadth).
3. Reference it in PRs that touch its path: `Journeys touched: J1, J5`.
4. When the Playwright rig lands, add the matching `frontend/journeys/jN.spec.js`.
