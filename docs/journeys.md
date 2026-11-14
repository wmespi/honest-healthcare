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
| **Sam** | Downtown Atlanta, on the network whose file prices hospitals' own fees | Anthem Gold Pathway PCP Copay Choice HMO 1200 (`49046GA0700055`), on Pathway |
| **Dana** | Comparison shopper with no plan yet | none |

## Flow A — find imaging care

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J1 | Rosa: what does a brain MRI cost near me? | Brain MRI → ZIP 30322 → her plan | Places on her plan ranked by the most she'd pay, each with its own name, a city and a distance; The Emory Clinic's whole price; her share of the rate; a hospital's own fee shown as missing, so it has no most you'd pay |
| J2 | Rosa: a knee MRI | Knee MRI → within 10 miles | Places with a whole price first, places missing part of their bill after |
| J3 | Sam: a brain MRI at a hospital | Brain MRI → ZIP 30303 → their plan → Grady Memorial Hospital | The hospital's fee from the plan's file and the reading add up to the most they'd pay, and the row says what the hospital charges without insurance |

## Flow A — find care with one bill

Each is one service of its family, on Rosa's plan from ZIP 30322.
Expected for all: the place shows one part at its rate, which is the most she'd pay, no hospital is listed, and her share is the plan's for that kind of care.

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J5 | Rosa: an office visit | Office visits → Office visit, returning patient, 20 to 29 minutes | The Emory Clinic's visit; her share shown for primary care and for a specialist |
| J6 | Rosa: a yearly checkup | Yearly checkups → Yearly checkup, returning patient, age 40 to 64 | The Emory Clinic's checkup; her share is preventive care's |
| J7 | Rosa: urgent care | Urgent care → Urgent care visit, returning patient, 20 to 29 minutes → within 10 miles | Only urgent care centers, Peachtree Immediate Care among them |
| J8 | Rosa: counseling | Mental health counseling → Talk therapy, about 45 minutes | The Emory Clinic's session |
| J9 | Rosa: physical therapy | Physical and occupational therapy → Exercise therapy, per 15 minutes | The Emory Clinic's rate for 15 minutes |
| J10 | Rosa: a lab test | Lab tests → Complete blood count, basic (CBC) | The Emory Clinic's rate for the test |

## No plan

| ID | Journey | Expected |
|---|---|---|
| J4 | Dana: just looking | The screen asks for a plan, since prices depend on it, and the API refuses a search without one |

## Flow B — pick a plan (not built; needs multi-payer MRFs + CMS Exchange cost-sharing)

Least all-in cost across plans for a knee replacement + two visits · which plans
cover drug X at what tier · are my three doctors in-network on this plan.

## Adding a journey

1. Add its row here (persona, clickpath, expected).
2. Add a pointed assertion to `tests/live/journeys.py` — the *specific* expected outcome
   (exact rate, benchmark band, tier), not a broad basket (`test_golden.py` is breadth).
3. Cite it in PRs that touch its path: `Journeys touched: J1, J2`.
