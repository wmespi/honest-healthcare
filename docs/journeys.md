# User journeys

*Named "a real person does a real thing" walks through the app. A journey
is the sanity check a PR author walks before merge (`Journeys touched:` in the PR
body). Current pass/fail and latency come from `make test-live`
(`tests/live/journeys.py`) - this file defines the journeys, it does not report on them.*

Each journey starts at `/find-care`, which every other address redirects to; prefix it
with your stack's host (`localhost:8000`). The page keeps the search in its URL, so a
journey can start from one (coverage: `application/frontend/src/findCare/*.test.jsx`).

| Persona | Who | Plan |
|---|---|---|
| **Rosa** | 63, near retirement, individual HMO in Georgia - the primary use case ([AGENTS.md](../AGENTS.md)) | Anthem Silver Blue Value HMO 4000 (`45334GA0020002`), on Blue Value |
| **Sam** | Downtown Atlanta, on one of the Pathway networks, whose files price hospitals' own fees | Anthem Gold Pathway PCP Copay Choice HMO 1200 (`49046GA0700055`), on Pathway |
| **Jordan** | 26, on a catastrophic plan, whose copay covers only the first few primary care visits | Anthem Catastrophic Pathway Guided Access HMO 10600 First 3 PCP Visits $40 (`49046GA0700010`), on Pathway Guided Access |
| **Lee** | Near Emory, insured by Ambetter rather than Anthem | Ambetter Standard Silver HMO $6000 $40 (`70893GA0010077`), on Premier |
| **Dana** | Comparison shopper with no plan yet | none |

## Flow A - find imaging care

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J1 | Rosa: what does a brain MRI cost near me? | Brain MRI → ZIP 30322 → her plan | Places on her plan ranked by the most she'd pay, each with its own name, a city and a distance; The Emory Clinic's whole price; her share of the rate; a hospital's own fee shown as missing, so it has no most you'd pay |
| J2 | Rosa: a knee MRI | Knee MRI → within 10 miles | Places with a whole price first, places missing part of their bill after |
| J3 | Sam: a brain MRI at a hospital | Brain MRI → ZIP 30303 → their plan → Grady Memorial Hospital | The hospital's fee from the plan's file and the reading add up to the most they'd pay, and the row says what the hospital charges without insurance |

## Flow A - find care with one bill

Each is one service of its family, on Rosa's plan from ZIP 30322.
Expected for all: the place shows one part at its rate, which is the most she'd pay, no hospital is listed, and her share is the plan's for that kind of care.

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J5 | Rosa: a primary care visit | Office visits → Office visit, returning patient, 20 to 29 minutes → Primary care | The Emory Clinic's visit; her share is primary care's |
| J17 | Rosa: a visit with a skin doctor | Office visits → Office visit, returning patient, 20 to 29 minutes → Skin | Dermatology Associates of Georgia's visit; her share is a specialist's |
| J6 | Rosa: a yearly checkup | Yearly checkups → Yearly checkup, returning patient, age 40 to 64 | The Emory Clinic's checkup; her share is preventive care's |
| J7 | Rosa: urgent care | Urgent care → Urgent care visit, returning patient, 20 to 29 minutes → within 10 miles | Only urgent care centers, Peachtree Immediate Care among them |
| J8 | Rosa: counseling | Mental health counseling → Talk therapy, about 45 minutes | The Emory Clinic's session |
| J9 | Rosa: physical therapy | Physical and occupational therapy → Exercise therapy, per 15 minutes | The Emory Clinic's rate for 15 minutes |
| J10 | Rosa: a lab test | Lab tests → Complete blood count, basic (CBC) | The Emory Clinic's rate for the test |

## Flow A - find a procedure

Each is one service of its family, from ZIP 30322.
Expected for all: the place shows the facility's fee and the doctor's as separate bills, each saying where its rate is from, the most they'd pay is their sum only when neither is missing, and the share is the plan's for outpatient surgery and the outpatient facility.

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J11 | Sam: a colonoscopy at a hospital | Colonoscopy and upper endoscopy → Colonoscopy, screening or diagnostic → their plan | Emory University Hospital Midtown's fee from its own price list and the doctor's fee from the plan's file add up to the most they'd pay |
| J12 | Rosa: a colonoscopy | Colonoscopy and upper endoscopy → Colonoscopy, screening or diagnostic → her plan | The Emory Clinic's doctor's fee, with the facility's fee missing, since her plan's file lists none |
| J13 | Sam: cataract surgery | Eye surgery and injections → Cataract removal with a new lens → their plan | Eye Physicians and Surgeons, a surgery center, with its own fee and the doctor's, both from the plan's file |
| J14 | Sam: a knee scope | Bone, joint and spine procedures → Knee scope with meniscus removal, one side → their plan | Emory University Hospital's fee from its own price list, with the doctor's fee missing |
| J15 | Rosa: a skin biopsy | Skin procedures → Skin biopsy, one spot, shaved → her plan | Dermatology Associates of Georgia's office rate, which includes the room, as the one bill |
| J16 | Sam: gallbladder removal | Other surgery → Gallbladder removal by scope → their plan | Emory University Hospital's fee from its own price list, with the doctor's fee missing |

## Quality

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J18 | Sam: is this place any good for a colonoscopy? | Colonoscopy, screening or diagnostic → their plan → Emory University Hospital Midtown | Its hospital visits within 7 days after a colonoscopy, compared with the national rate, with the period and source, and how many colonoscopies its doctors have done |
| J19 | Sam and Rosa: what else will I be billed? | Colonoscopy with biopsy → each plan | Outside the places' prices: on Sam's plan the usual anesthesia and lab-exam rates; on Rosa's the lab exam, and a note that her plan lists no anesthesia rate |

## Flow A - a hospital stay

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J20 | Sam: having a baby | Hospital stays → Having a baby, vaginal delivery → ZIP 30303 → their plan → within 10 miles | Emory University Hospital Midtown's stay at the plan's price, Grady's paid $8,000 a day from its own price list with no total, and the doctor and anesthesia billed separately |
| J21 | Rosa: having a baby | Hospital stays → Having a baby, vaginal delivery → her plan | No hospital shows a price, the screen says her plan's file lists none, and it shows what her plan usually pays the doctor and for an epidural |

## Flow A - a catastrophic plan

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J22 | Jordan: a primary care visit | Office visits → Office visit, returning patient, 20 to 29 minutes → Primary care → their plan | The Emory Clinic's visit; their share is a $40 copay for the first visits the plan covers before the deductible, then the whole rate until the deductible |

## Flow A - another insurer

| ID | Journey | Clickpath | Expected |
|---|---|---|---|
| J23 | Lee: cataract surgery | Eye surgery and injections → Cataract removal with a new lens → their plan | Emory University Hospital's surgeon's fee from the plan's file, which prices it only for the right or left eye, the highest of the groups whose surgeons CMS affiliates with the hospital, and the hospital's fee from its own price file, so the most they'd pay is the two together |
| J24 | Lee: physical therapy | Physical and occupational therapy → Exercise therapy, per 15 minutes → their plan | Independent Physical Therapy of Georgia's rate for 15 minutes, which the plan's file gives only under a therapist's plan of care; their share is a copay |

## No plan

| ID | Journey | Expected |
|---|---|---|
| J4 | Dana: just looking | The screen asks for a plan, since prices depend on it, and the API refuses a search without one |

## Flow B - pick a plan (not built; needs multi-payer MRFs + CMS Exchange cost-sharing)

Least all-in cost across plans for a knee replacement + two visits · which plans
cover drug X at what tier · are my three doctors in-network on this plan.

## Adding a journey

1. Add its row here (persona, clickpath, expected).
2. Add a pointed assertion to `tests/live/journeys.py` - the *specific* expected outcome
   (exact rate, benchmark band, tier), not a broad basket (`test_golden.py` is breadth).
3. Cite it in PRs that touch its path: `Journeys touched: J1, J2`.
