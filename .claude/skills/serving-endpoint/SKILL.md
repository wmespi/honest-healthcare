---
name: serving-endpoint
description: >
  Add or modify a FastAPI route in the honest-healthcare serving layer. Use when the
  user wants a new API endpoint, a change to an existing one, or a new query over
  the rate/provider data.
---

# Adding / changing a serving endpoint

Read `serving/serving.md` — it holds the router layout, the shared helpers
(`db()`, `RATE_GROUPS_SRC`, `rate_filters`, `network_slug`), the query rules that
hang if you get them wrong, and the finishing checklist. Schema: `docs/schema.md`.

Then: add the route in the right `serving/routers/*.py`, add its contract test, and
run `make test`.
