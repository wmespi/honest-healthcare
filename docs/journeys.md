# User journeys

*Named "a real person does a real thing" walks through the app. A journey
is the sanity check a PR author walks before merge (`Journeys touched:` in the PR
body). Current pass/fail and latency come from `make test-live`
(`tests/live/journeys.py`) — this file defines the journeys, it does not report on them.*

Each journey starts at `/find-care/imaging`, which every other address redirects to;
prefix it with your stack's host (`localhost:5173`). The page keeps your plan between
visits but doesn't read one from the URL
(coverage: `frontend/src/routes/*.test.jsx`).

| Persona | Who | Plan |
|---|---|---|
| **Rosa** | 63, near retirement, individual HMO in Georgia — the primary use case ([AGENTS.md](../AGENTS.md)) | `GA Blue Value HIX Individual Network` |
| **Dana** | Comparison shopper with no plan yet | none (all networks) |

## Flow A — find imaging care

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J1 | Rosa: what does a brain MRI cost near me? | plan gate → Brain MRI → ZIP 30322 | Practices ranked by price and distance, each with its own name, a city and a distance; a practice with one contract shows one price, not a range; the hospital fee shows as a labelled estimate |
| J2 | Rosa: a knee MRI | scan picker → results | Complete (technical + professional) prices first, part-prices after |

## Browse — no plan

| ID | Journey | Expected |
|---|---|---|
| J4 | Dana: just looking | A clear signal a plan is needed for real numbers; no price shown without one |

## Flow B — pick a plan (not built; needs multi-payer MRFs + CMS Exchange cost-sharing)

J6 least all-in cost across plans for a knee replacement + two visits · J7 which plans
cover drug X at what tier · J8 are my three doctors in-network on this plan.

## Adding a journey

1. Add its row here (persona, clickpath, expected).
2. Add a pointed assertion to `tests/live/journeys.py` — the *specific* expected outcome
   (exact rate, benchmark band, tier), not a broad basket (`test_golden.py` is breadth).
3. Cite it in PRs that touch its path: `Journeys touched: J1, J2`.
