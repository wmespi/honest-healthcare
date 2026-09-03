---
name: state-of-the-build
description: >
  Produce the "State of the Build" brief for honest-healthcare — a re-orientation
  report for the owner covering what shipped recently, the open issues, where the
  agentic-dev gaps are, the user-journey status, and what needs a decision. Use
  when the owner asks to "catch me up", wants a "status report" / "state of the
  build" / "weekly report", says they feel disconnected from the project, or on a
  regular cadence. Output is a published Artifact.
---

# State of the Build

A synthesized read for the owner, who works through agents and periodically loses
the thread of what changed. It is **not** a changelog dump — it's a briefing:
what's live, what's wrong, what's next, ranked, with the owner's own numbered work
streams tracked over time. First edition + rationale for the format:
`docs/decisions/` (once #71 lands) and the original artifact linked from issue #71.

## When to run it

- The owner asks directly, or says "catch me up" / "I feel disconnected".
- On a cadence (target: weekly). #71 tracks a `make brief` target that will print
  the deterministic inputs; until then, gather them by hand as below.
- After a batch rollout of 3+ PRs — the disconnection risk is highest there.

## 1. Gather the inputs

Run these from the canonical checkout. Don't paraphrase from memory — the value
is that the numbers are real.

```bash
git fetch origin --tags -q

# what shipped since the last brief — use the last `brief-<date>` tag if one
# exists, else the last `tailnet-*` promote tag, else ~2 weeks
git log --oneline --no-merges <last-marker>..origin/main

make tiers                    # tailnet vs origin/main — the deploy gap
gh issue list --state open --limit 50
gh pr list --state open
make footprint | tail -20     # disk sanity — flag any ⚠
make journeys                 # journey pass/fail table  (once #72 lands)
```

Then **verify the product still answers correctly** — hit the live API for the
canary and 2–3 journey endpoints (`serving/` must be running):

```bash
N='GA%20Blue%20Value%20HIX%20Individual%20Network'
curl -s "http://localhost:8000/rates/quote?billing_code=99213&npi=1285125310&network_name=$N" | python3 -m json.tool
# expect: headline.rate 82.05 · medicare_allowed ~86.75 · vs_medicare ~0.95
```

If a canary has moved, that's the headline of the brief, not a footnote.

## 2. Structure the brief

Six sections, in this order (front-load the synthesis):

1. **Where things stand** — one paragraph on what the product *is* right now
   (Flow A / Flow B state), then: a "shipped recently" table (area · what landed ·
   PRs), the corpus numbers (`GET /` — priceable NPIs, codes, networks, as-of),
   the canary result, and an open-issues table with a "reads as" column
   (real bug / small fix / planning / epic).
2. **Agentic-dev health** — the practice scorecard (plan-first, fast gate, small
   PRs, context system, golden tests, journeys, decision log, review, visual
   regression, walk-the-app ritual, run telemetry) with a strong/weak/missing
   chip each, then the ranked gaps. Keep this stable edition-to-edition so the
   owner sees movement.
3. **User journeys** — the J1..Jn table with *real* current status from step 1,
   each with the rough edge if any. This is the "how are the full journeys doing"
   view.
4. **Frontend** — only if there's something to say; findings + a wireframe if
   proposing a change.
5. **Deploy / tiers** — `make tiers` output: what the tailnet serves, the
   unpromoted commits, anything waiting on a promote decision.
6. **What I did / what needs your call** — a done callout, then a short list of
   decisions blocked on the owner.

Each of the owner's numbered work streams (currently: 1 agentic-dev safety net,
2 journey harness, 3 frontend redesign, 4 issue triage, 5 two-tier deploy —
issues #71–74) carries its status forward so the brief doubles as a tracker.

## 3. Publish

Load the `artifact-design` skill, then build it as an HTML artifact:

- **Title stays exactly `State of the Build`** every edition — it's the same
  living document, redeployed. Pass the previous artifact's `url` so it updates in
  place rather than spawning a new one (find it with `Artifact` `action: "list"`).
- Utilitarian-polished treatment — a briefing document, not a landing page.
  Status chips (shipped / gap / proposed), tables with tabular numerals, mono
  wireframes. Light + dark.
- Date it in the masthead byline (`main @ <sha>`), and note which streams it
  covers.

## 4. Honesty rules

- Every number traces to a command in step 1. No "roughly" where a real count
  exists.
- Journey status is what the live API returned today, not what a PR claimed.
- If the product regressed, say so plainly and lead with it.
- Name gaps as gaps. The brief's job is to keep the owner able to *trust* the
  build, which means not overselling it.
