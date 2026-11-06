# User journeys

*Named "a real person does a real thing" walks through the app. A journey
is the sanity check a PR author walks before merge (`Journeys touched:` in the PR
body). Current pass/fail and latency come from `make test-live`
(`tests/live/journeys.py`) — this file defines the journeys, it does not report on them.*

Each journey starts at `/find-care`, which every other address redirects to; prefix it
with your stack's host (`localhost:8000`). The page keeps the search in its URL, so a
journey can start from one (coverage: `application/frontend/src/findCare/*.test.jsx`).

| Persona | Who | Plan |
|---|---|---|
| **Rosa** | 63, near retirement, individual HMO in Georgia — the primary use case ([AGENTS.md](../AGENTS.md)) | Anthem Silver Blue Value HMO 4000 (`45334GA0020002`), on Blue Value |
| **Dana** | Comparison shopper with no plan yet | none |

## Flow A — find imaging care

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J1 | Rosa: what does a brain MRI cost near me? | Brain MRI → ZIP 30322 → her plan | Places on her plan ranked by the most she'd pay, each with its own name, a city and a distance; The Emory Clinic's whole price; her share of the rate; a hospital's own fee shown as missing, so it has no most you'd pay |
| J2 | Rosa: a knee MRI | Knee MRI → within 10 miles | Places with a whole price first, places missing part of their bill after |

## No plan

| ID | Journey | Expected |
|---|---|---|
| J4 | Dana: just looking | The screen asks for a plan, since prices depend on it, and the API refuses a search without one |

## Flow B — pick a plan (not built; needs multi-payer MRFs + CMS Exchange cost-sharing)

J6 least all-in cost across plans for a knee replacement + two visits · J7 which plans
cover drug X at what tier · J8 are my three doctors in-network on this plan.

## Adding a journey

1. Add its row here (persona, clickpath, expected).
2. Add a pointed assertion to `tests/live/journeys.py` — the *specific* expected outcome
   (exact rate, benchmark band, tier), not a broad basket (`test_golden.py` is breadth).
3. Cite it in PRs that touch its path: `Journeys touched: J1, J2`.
