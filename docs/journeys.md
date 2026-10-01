# User journeys

*Named "a real person does a real thing" walks through the rate explorer. A journey
is the sanity check a PR author walks before merge (`Journeys touched:` in the PR
body). Current pass/fail and latency come from `make test-live`
(`scripts/journeys.py`) — this file defines the journeys, it does not report on them.*

Each Link is the app pre-loaded to that journey (`?plan=&specialty=&npi=&code=`,
or `?bypass=1` for no plan); prefix it with your stack's host (`localhost:5173`).
The address bar stays in sync as you navigate, so any page state is a copyable URL.
`/` is the task-first landing; J1–J5 target `/explore`. The PCP flow lives at
`/find-care/pcp` (coverage: `frontend/src/routes/*.test.jsx`).

| Persona | Who | Plan |
|---|---|---|
| **Rosa** | 63, near retirement, individual HMO in Georgia — the primary use case ([AGENTS.md](../AGENTS.md)) | `GA Blue Value HIX Individual Network` |
| **Dana** | Comparison shopper with no plan yet | none (all networks) |

## Flow A — find care

| ID | Journey | Link | Clickpath | Expected |
|---|---|---|---|---|
| J1 | Rosa: what does a routine check-up cost? | `/explore?plan=GA Blue Value HIX Individual Network&npi=1285125310&code=99213` | plan gate → Family Medicine → provider → provider menu → `99213` | One dollar figure, a Medicare benchmark line, "you'd pay ≈" once cost-sharing is entered |
| J2 | Rosa: a knee MRI | `…&npi=1285125310&code=73721` | procedure search → distribution → provider compare or one cost card | Rate range, professional/technical split explained, by-setting spread |
| J3 | Rosa: is my doctor in this plan? | `…&npi=1285125310` | provider search by name → "has rates" / "not in plan" badge | Answer without a dead-end quote screen; no-rate providers render inert |
| J5 | Rosa: a screening colonoscopy | `…&npi=1407147028&code=45378` | Gastroenterology → provider → `45378` | Rate, Medicare benchmark, an honest "group's rate, not verified to this provider" caveat |

## Browse — no plan

| ID | Journey | Link | Expected |
|---|---|---|---|
| J4 | Dana: just looking | `/explore?bypass=1` | No misleading aggregate presented as a finding; a clear signal a plan is needed for real numbers |

## Flow B — pick a plan (not built; needs multi-payer MRFs + CMS Exchange cost-sharing)

J6 least all-in cost across plans for a knee replacement + two visits · J7 which plans
cover drug X at what tier · J8 are my three doctors in-network on this plan.

## Adding a journey

1. Add its row here (persona, link, clickpath, expected).
2. Add a pointed assertion to `scripts/journeys.py` — the *specific* expected outcome
   (exact rate, benchmark band, tier), not a broad basket (`test_golden.py` is breadth).
3. Cite it in PRs that touch its path: `Journeys touched: J1, J5`.
